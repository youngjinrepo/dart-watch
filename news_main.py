"""
미국 주식 밸류체인 & SEC EDGAR 공시 인텔리전스 레이더 (1시간 주기 실시간 감시)
- config.json의 news.us_tickers (예: ["CRDO"])에 티커만 넣으면:
  1) [AI 프로파일러] 100% 영문 추론(Thinking)으로 해당 기업의 핵심 제품 용어(AEC, SerDes 등), 주요 고객사,
     구조적 병목·리스크, 경쟁사를 도출해 3계층(Layer 1~3) 영문 검색 쿼리 자동 생성 및 캐싱
  2) [미국 SEC EDGAR 실시간 공시] API 키 없이 SEC 공식 서버(data.sec.gov)에서
     핵심 공시(8-K 수시공시, 10-Q/10-K 실적보고서, Form 4 임원·내부자 지분 매매, SC 13D/G 대주주 지분) 실시간 감지 및 원문 파싱
  3) [미국 현지 3계층 뉴스 수집] Google News US (영문 RSS)에서 직접 이슈 / 전방 수요·CapEx / 생태계·경쟁사 뉴스 수집
  4) [스마트 이력 관리 (1시간 텀 최적화)]
     - 한 번 평가한 기사(채택된 기사 + 걸러진 클릭베이트 기사 모두)와 SEC 공시 번호를 이력(news_sent.json)에 기록
     - 1시간 뒤 실행될 때 새로 올라온 기사나 공시가 없으면 AI를 호출하지 않고 1초 만에 조용히 종료
"""

import os
import re
import time
import html
import json
import argparse
import warnings
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

KST = timezone(timedelta(hours=9))

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

CONFIG_FILE = Path("config.json")

# SEC EDGAR 공식 API 필수 User-Agent 헤더 (형식: AppName/Version (ContactEmail))
SEC_HEADERS = {
    "User-Agent": "DartWatchIntelligenceBot/2.0 (admin@dartwatch.local)",
    "Accept-Encoding": "gzip, deflate",
}

# 감시할 핵심 SEC 공시 양식
TARGET_SEC_FORMS = {"8-K", "8-K/A", "10-Q", "10-K", "4", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}

GEMINI_MODELS = [
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
]


def load_config() -> Dict[str, Any]:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_news_state(path: Path) -> Dict[str, List[str]]:
    """
    뉴스 및 SEC 공시 발송/검토 이력 로드 (하위 호환 지원)
    - seen_articles: 이미 수집·AI 평가를 거친 모든 기사 키 (채택/탈락 포함 -> 1시간 뒤 중복 재평가 방지)
    - sent_articles: 실제로 텔레그램 발송된 기사 키
    - seen_sec_filings: 이미 확인·발송한 SEC EDGAR 공시 고유번호(accessionNumber)
    """
    raw = load_json_file(path, {})
    if isinstance(raw, list):
        return {
            "seen_articles": list(raw),
            "sent_articles": list(raw),
            "seen_sec_filings": [],
        }
    if not isinstance(raw, dict):
        raw = {}
    return {
        "seen_articles": list(raw.get("seen_articles", [])),
        "sent_articles": list(raw.get("sent_articles", [])),
        "seen_sec_filings": list(raw.get("seen_sec_filings", [])),
    }


def save_news_state(path: Path, state: Dict[str, List[str]], max_history: int = 500) -> None:
    cleaned = {
        "seen_articles": list(dict.fromkeys(state.get("seen_articles", [])))[-max_history:],
        "sent_articles": list(dict.fromkeys(state.get("sent_articles", [])))[-max_history:],
        "seen_sec_filings": list(dict.fromkeys(state.get("seen_sec_filings", [])))[-max_history:],
    }
    save_json_file(path, cleaned)


def call_gemini_json(
    prompt: str,
    max_tokens: int = 2048,
    temperature: float = 0.1,
    thinking_budget: int = 0,
) -> Optional[Dict[str, Any]]:
    """Gemini REST API 호출 (JSON 스키마 응답 + 모델 폴백 + 재시도)"""
    if not GEMINI_API_KEY or GEMINI_API_KEY.startswith("your-"):
        print("❌ GEMINI_API_KEY가 설정되지 않았습니다.")
        return None

    for model_name in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        gen_cfg: Dict[str, Any] = {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
            "responseMimeType": "application/json",
        }
        if "flash-lite" not in model_name:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": thinking_budget}

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_cfg,
        }

        for attempt in range(1, 3):
            try:
                resp = requests.post(
                    url,
                    headers={"x-goog-api-key": GEMINI_API_KEY},
                    json=payload,
                    timeout=60,
                )
                if resp.status_code == 429:
                    print(f"⏳ [{model_name}] 무료 쿼터 도달(429) -> 다음 모델로 자동 전환...")
                    break
                if resp.status_code == 503 and attempt < 2:
                    time.sleep(3)
                    continue
                resp.raise_for_status()
                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    break
                parts = candidates[0].get("content", {}).get("parts", [])
                raw_text = "\n".join(p.get("text", "") for p in parts if not p.get("thought") and "text" in p).strip()
                return json.loads(raw_text)
            except Exception as e:
                if attempt < 2:
                    time.sleep(2)
                    continue
                print(f"⚠️ [{model_name}] 호출 실패 ({e.__class__.__name__})")
    return None


def resolve_sec_cik(ticker: str, profile: Dict[str, Any]) -> Optional[str]:
    """티커(예: CRDO)에 해당하는 미국 SEC 10자리 CIK 번호 조회"""
    if profile.get("sec_cik"):
        return str(profile["sec_cik"]).zfill(10)
    try:
        resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_HEADERS, timeout=12)
        resp.raise_for_status()
        tickers_map = resp.json()
        target = ticker.upper().strip()
        for item in tickers_map.values():
            if str(item.get("ticker", "")).upper() == target:
                cik = str(item.get("cik_str", "")).zfill(10)
                profile["sec_cik"] = cik
                return cik
    except Exception as e:
        print(f"⚠️ [{ticker}] SEC CIK 번호 조회 실패: {e}")
    return None


def _parse_sec_form4_summary(cik_int: int, acc_no_clean: str, primary_doc: str) -> str:
    """SEC Form 4 (임원/내부자 지분 매매) XML에서 보고자, 직책, 매수/매도 수량 및 단가 요약 추출"""
    raw_xml_name = primary_doc.split("/")[-1]
    xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_clean}/{raw_xml_name}"
    try:
        resp = requests.get(xml_url, headers=SEC_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")

        owner = soup.find("rptownername").get_text(strip=True) if soup.find("rptownername") else "Insider"
        title_tag = soup.find("officertitle")
        is_dir = soup.find("isdirector")
        role = title_tag.get_text(strip=True) if title_tag and title_tag.get_text(strip=True) else (
            "Director" if is_dir and is_dir.get_text(strip=True) in ("1", "true") else "Insider"
        )

        tx_summaries: List[str] = []
        code_map = {
            "P": "Open-Market Purchase (장내매수)",
            "S": "Open-Market Sale (장내매도)",
            "M": "Option Exercise (스톡옵션 행사)",
            "A": "Grant/Award (주식 보상 부여)",
            "F": "Tax Withholding (세금납부용 처분)",
            "G": "Gift (증여)",
        }
        for tx in soup.find_all("nonderivativetransaction")[:4]:
            code_tag = tx.find("transactioncode")
            shares_tag = tx.find("transactionshares")
            price_tag = tx.find("transactionpricepershare")

            code = code_tag.get_text(strip=True) if code_tag else "?"
            shares_val = shares_tag.find("value").get_text(strip=True) if (shares_tag and shares_tag.find("value")) else "?"
            price_val = price_tag.find("value").get_text(strip=True) if (price_tag and price_tag.find("value")) else ""

            code_desc = code_map.get(code, f"Code {code}")
            price_str = f" @ ${price_val}" if price_val else ""
            tx_summaries.append(f"{code_desc}: {shares_val} shares{price_str}")

        tx_text = "; ".join(tx_summaries) if tx_summaries else "Insider equity transaction"
        return f"Insider: {owner} ({role}) | {tx_text}"
    except Exception:
        return "Form 4 Insider Transaction Report"


def _parse_sec_doc_snippet(cik_int: int, acc_no_clean: str, primary_doc: str, max_chars: int = 1200) -> str:
    """8-K, 10-Q, 10-K 등 SEC 공시 원문 앞부분 핵심 텍스트 추출"""
    doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_clean}/{primary_doc}"
    try:
        resp = requests.get(doc_url, headers=SEC_HEADERS, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))
        return text[:max_chars]
    except Exception:
        return ""


def fetch_sec_edgar_filings(
    ticker: str,
    profile: Dict[str, Any],
    seen_filings: Set[str],
    lookback_days: int = 7,
) -> Tuple[List[Dict[str, str]], List[str]]:
    """
    SEC EDGAR 공식 API에서 최근 핵심 공시(8-K, 10-Q, 10-K, Form 4, SC 13D/G) 조회
    - 반환값: (신규 공시 리스트, 현재 존재하는 전체 최근 공시 번호 리스트[초기 시드용])
    """
    cik = resolve_sec_cik(ticker, profile)
    if not cik:
        return [], []

    cik_int = int(cik)
    sub_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = requests.get(sub_url, headers=SEC_HEADERS, timeout=12)
        resp.raise_for_status()
        recent = resp.json().get("filings", {}).get("recent", {})

        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        acc_nums = recent.get("accessionNumber", [])
        prim_docs = recent.get("primaryDocument", [])
        items_list = recent.get("items", [])

        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        new_filings: List[Dict[str, str]] = []
        all_recent_acc: List[str] = []

        for i in range(min(len(forms), 40)):
            form = str(forms[i]).strip()
            f_date = str(filing_dates[i]).strip()
            acc_no = str(acc_nums[i]).strip()
            prim_doc = str(prim_docs[i]).strip()
            items_str = str(items_list[i]).strip() if i < len(items_list) else ""

            if form not in TARGET_SEC_FORMS:
                continue

            all_recent_acc.append(acc_no)

            if acc_no in seen_filings or f_date < cutoff_date:
                continue

            acc_clean = acc_no.replace("-", "")
            filing_link = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{prim_doc}"

            if form == "4":
                detail = _parse_sec_form4_summary(cik_int, acc_clean, prim_doc)
            else:
                snippet = _parse_sec_doc_snippet(cik_int, acc_clean, prim_doc, max_chars=900)
                detail = f"Items: {items_str} | Excerpt: {snippet}" if items_str else f"Excerpt: {snippet}"

            new_filings.append({
                "accession_number": acc_no,
                "form": form,
                "filing_date": f_date,
                "link": filing_link,
                "detail": detail,
            })

            if len(new_filings) >= 5:
                break

        return new_filings, all_recent_acc[:30]
    except Exception as e:
        print(f"⚠️ [{ticker}] SEC EDGAR 공시 조회 실패: {e}")
        return [], []


def get_or_build_ticker_profile(ticker: str, cache_file: Path, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    """
    종목 티커(예: CRDO)에 대한 3계층 밸류체인 검색 프로필을 로드하거나,
    캐시에 없으면 100% 영문 추론(Thinking)을 통해 자동 생성하여 저장합니다.
    """
    ticker = ticker.upper().strip()
    profiles = load_json_file(cache_file, {})

    if not force_refresh and ticker in profiles:
        if not profiles[ticker].get("sec_cik"):
            cik = resolve_sec_cik(ticker, profiles[ticker])
            if cik:
                save_json_file(cache_file, profiles)
        return profiles[ticker]

    print(f"🧠 [{ticker}] 영문 딥리서치(Thinking)로 밸류체인 & 연관 키워드 자동 도출 중...")
    prompt = f"""You are a Senior Wall Street Equity Research Analyst covering US-listed equities.
Your task is to build a comprehensive 3-Layer Intelligence & Search Profile for the US stock ticker: "{ticker}".

Do NOT rely on generic industry clichés. Think deeply from first principles (10-K revenue breakdown, unit economics, customer concentration, upstream bottlenecks, and technological substitution risks) for "{ticker}":
1. What are the exact products/services that drive "{ticker}"'s revenue and margins? What niche technical terms or acronyms do industry insiders use?
2. Who are the actual buyers/customers, and what financial or operational indicators signal that those buyers are accelerating or slowing down their spending/orders?
3. What are the physical, financial, regulatory, or architectural bottlenecks and substitute threats that could disrupt "{ticker}"'s growth thesis?
4. Who are the direct competitors and supply-chain peers whose earnings or product announcements act as leading indicators for "{ticker}"?

Design highly targeted English search queries for Google News US RSS.
IMPORTANT:
- Keep each query concise (using 2 to 4 key English terms) so Google News RSS returns actual recent articles.
- Do NOT include year numbers (e.g., 2024, 2025) in search queries because recency is already filtered automatically.

Return ONLY valid JSON matching this exact schema:
{{
  "ticker": "{ticker}",
  "company_name": "Full official English company name",
  "korean_summary": "2-sentence Korean summary of the core business model and primary stock price drivers",
  "key_products_and_terms": ["Exact product/tech term 1", "Exact product/tech term 2", "Exact product/tech term 3"],
  "key_customers": ["Major customer or buyer segment 1", "Major customer or buyer segment 2"],
  "key_competitors": ["Competitor 1 (Ticker)", "Competitor 2 (Ticker)", "Competitor 3 (Ticker)"],
  "hidden_risks_and_bottlenecks": [
    "Key demand slowdown or budget exhaustion risk (in English)",
    "Key technological substitution or bottleneck risk (in English)"
  ],
  "queries": {{
    "layer1_direct": [
      "Direct company/ticker query 1",
      "Direct company/ticker + product or earnings query 2"
    ],
    "layer2_macro_capex": [
      "Downstream customer spending / budget expansion or slowdown query 1",
      "Customer financial health / ROI / financing / bottleneck query 2",
      "Macro or sector-wide demand cycle query 3"
    ],
    "layer3_ecosystem": [
      "Core niche product / technology adoption or substitution query 1",
      "Key competitor or peer group catalyst query 2",
      "Supply chain / industry standard / architecture shift query 3"
    ]
  }}
}}
"""
    profile = call_gemini_json(prompt, max_tokens=2048, temperature=0.1, thinking_budget=1024)
    if profile and "queries" in profile:
        resolve_sec_cik(ticker, profile)
        profiles[ticker] = profile
        save_json_file(cache_file, profiles)
        print(f"✅ [{ticker}] 밸류체인 프로필 생성 및 캐싱 완료 ({cache_file})")
        return profile
    return None


def fetch_us_google_news(query: str, lookback_days: int = 2, limit: int = 8) -> List[Dict[str, str]]:
    """Google News US (영문) RSS에서 검색어로 최신 미국 현지 기사 수집"""
    encoded_q = urllib.parse.quote(f"{query} when:{lookback_days}d")
    url = f"https://news.google.com/rss/search?q={encoded_q}&hl=en-US&gl=US&ceid=US:en"

    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
        items = soup.find_all("item")

        results: List[Dict[str, str]] = []
        for item in items[:limit]:
            raw_title = item.find("title").get_text(strip=True) if item.find("title") else ""
            link = item.find("link").get_text(strip=True) if item.find("link") else ""
            if not link and item.find("link") and item.find("link").next_sibling:
                link = str(item.find("link").next_sibling).strip()
            pub_date = item.find("pubdate").get_text(strip=True) if item.find("pubdate") else ""
            source_tag = item.find("source")
            publisher = source_tag.get_text(strip=True) if source_tag else ""

            clean_title = raw_title
            if publisher and clean_title.endswith(f" - {publisher}"):
                clean_title = clean_title[: -len(f" - {publisher}")].strip()
            elif " - " in clean_title:
                base_title, tail_pub = clean_title.rsplit(" - ", 1)
                if 1 < len(tail_pub) <= 40:
                    clean_title = base_title.strip()
                    if not publisher:
                        publisher = tail_pub.strip()

            if clean_title:
                results.append({
                    "title": clean_title,
                    "publisher": publisher or "US Media",
                    "pub_date": pub_date,
                    "link": link,
                    "query": query,
                })
        return results
    except Exception as e:
        print(f"⚠️ 미국 뉴스 RSS 조회 실패 ({query}): {e}")
        return []


def collect_layered_articles(
    profile: Dict[str, Any],
    seen_keys: Set[str],
    lookback_days: int = 2,
    max_per_layer: int = 8,
) -> Tuple[Dict[str, List[Dict[str, str]]], List[str]]:
    """
    3계층 쿼리를 순회하며 신규 미국 기사 수집 (이미 검토한 seen_keys 제외)
    - 반환값: (계층별 신규 기사 dict, 이번 실행에서 수집된 모든 신규 기사 norm_key 리스트)
    """
    queries_cfg = profile.get("queries", {})
    collected: Dict[str, List[Dict[str, str]]] = {
        "layer1_direct": [],
        "layer2_macro_capex": [],
        "layer3_ecosystem": [],
    }
    batch_keys: List[str] = []
    batch_seen: Set[str] = set()

    for layer_name in ["layer1_direct", "layer2_macro_capex", "layer3_ecosystem"]:
        q_list = queries_cfg.get(layer_name, [])
        layer_items: List[Dict[str, str]] = []
        for q in q_list:
            fetched = fetch_us_google_news(q, lookback_days=lookback_days, limit=max_per_layer)
            for art in fetched:
                norm_title = re.sub(r"\W+", "", art["title"].lower())[:60]
                if not norm_title or norm_title in batch_seen or norm_title in seen_keys:
                    continue
                batch_seen.add(norm_title)
                batch_keys.append(norm_title)
                art["norm_key"] = norm_title
                layer_items.append(art)
                if len(layer_items) >= max_per_layer:
                    break
            if len(layer_items) >= max_per_layer:
                break
        collected[layer_name] = layer_items

    return collected, batch_keys


def analyze_intelligence_for_ticker(
    profile: Dict[str, Any],
    sec_filings: List[Dict[str, str]],
    layered_articles: Dict[str, List[Dict[str, str]]],
) -> Optional[Dict[str, Any]]:
    """
    신규 SEC EDGAR 공시 및 1~3계층 영문 기사들을 바탕으로 종목 관점의 영향도와 연결고리 분석
    """
    ticker = profile.get("ticker", "")
    company_name = profile.get("company_name", ticker)
    kr_summary = profile.get("korean_summary", "")
    products = ", ".join(profile.get("key_products_and_terms", []))
    customers = ", ".join(profile.get("key_customers", []))
    competitors = ", ".join(profile.get("key_competitors", []))
    risks = " / ".join(profile.get("hidden_risks_and_bottlenecks", []))

    # 1) SEC 공시 블록 구성
    sec_lines: List[str] = []
    sec_map: Dict[int, Dict[str, str]] = {}
    if sec_filings:
        for s_idx, sf in enumerate(sec_filings, 1):
            sec_map[s_idx] = sf
            sec_lines.append(
                f"  [SEC #{s_idx}] Form {sf['form']} ({sf['filing_date']}) | {sf['detail']}"
            )
    else:
        sec_lines.append("  (No new SEC filings in this window)")

    # 2) 뉴스 기사 블록 구성
    article_lines: List[str] = []
    idx_map: Dict[int, Dict[str, str]] = {}
    counter = 1

    layer_titles = {
        "layer1_direct": "Layer 1: Direct Company Mentions",
        "layer2_macro_capex": "Layer 2: Downstream Customer Budget, Macro & Bottlenecks",
        "layer3_ecosystem": "Layer 3: Core Tech, Supply Chain & Competitors",
    }

    for layer_key, header in layer_titles.items():
        arts = layered_articles.get(layer_key, [])
        article_lines.append(f"\n[{header}]")
        if not arts:
            article_lines.append("  (No new articles)")
            continue
        for a in arts:
            idx_map[counter] = a
            article_lines.append(f"  #{counter} [{a['publisher']}] {a['title']} ({a['pub_date']})")
            counter += 1

    if not sec_map and not idx_map:
        return None

    sec_block = "\n".join(sec_lines)
    articles_block = "\n".join(article_lines)

    prompt = f"""You are a Senior Wall Street Equity Research Analyst.
Target Stock: {ticker} ({company_name})
- Business Summary: {kr_summary}
- Core Products & Niche Terms: {products}
- Key Customers / Buyers: {customers}
- Key Competitors / Peers: {competitors}
- Key Structural Risks & Bottlenecks to Watch: {risks}

Below are the newly detected US SEC EDGAR filings and US English news articles from the latest monitoring cycle.

CRITICAL RULES:
1. SEC FILINGS: If any SEC filings (`[SEC #1]`, etc.) are listed, analyze EVERY ONE of them in `sec_alerts`.
   - For `Form 4` (Insider Transactions): Clearly state who (Name & Title) bought (`P`), sold (`S`), or exercised/withheld (`M`/`F`) how many shares and whether it is a meaningful open-market signal or routine tax/compensation activity.
   - For `8-K`, `10-Q`, `10-K`, `SC 13D/G`: Summarize the exact material event or financial update.
2. NEWS SELECTION (Strict Quality Gate for Hourly Monitoring):
   - Select up to 5 genuinely high-signal articles in `selected_news`.
   - If all newly collected articles in this cycle are trivial clickbait, generic stock-mover recaps, or irrelevant noise, return an empty list `[]` for `selected_news` so we do NOT spam the user.
3. SECOND-ORDER REASONING:
   - Even if a Layer 2 or Layer 3 article does NOT mention "{ticker}" by name, explain the exact second-order causal chain connecting that news to "{ticker}"'s future revenue, orders, margins, or valuation multiples.
   - Do NOT blindly label "record customer spending" as bullish (`🟢 호재`) if the article warns that customer spending is outpacing operating cash flow, relying on debt financing, facing ROI skepticism, or hitting physical bottlenecks. Recognize those as **leading warning signals of future demand/budget slowdown** and classify them as `🟡 주의/체크` or `🔴 악재`.
4. Write all final JSON text fields in natural, institutional-grade Korean.

Return ONLY valid JSON matching this schema:
{{
  "overall_sentiment": "🟢 호재 우세 | 🟡 혼조/관망 | 🔴 수요·업황 주의 중 택 1",
  "executive_summary": "{ticker} 투자자를 위한 핵심 풍향계 2줄 종합 요약 (한국어)",
  "sec_alerts": [
    {{
      "sec_id": SEC 공시 번호(정수, 예: 1),
      "impact_signal": "🟢 호재 | 🟡 주의/체크 | 🔴 악재 | ⚪ 중립 중 택 1",
      "korean_title": "공시 핵심 제목 (예: CTO 장내 매도 15,000주 / 8-K 주주총회 결과 공시)",
      "summary": "투자자 관점의 공시 핵심 내용 및 시사점 1~2문장 설명 (한국어)"
    }}
  ],
  "selected_news": [
    {{
      "article_id": 선택한 기사 번호(정수, 예: 1),
      "layer_badge": "🎯 직접 이슈 | 💰 전방 수요·자본 | 🔗 밸류체인·경쟁사 중 택 1",
      "impact_signal": "🟢 호재 | 🟡 주의/체크 | 🔴 악재 | ⚪ 중립 중 택 1",
      "korean_headline": "미국 기사 핵심 제목 한국어 번역",
      "why_it_matters": "필수 작성(빈 문자열 금지): {ticker} 주주 관점의 핵심 내용 및 2차 연결고리(인과관계) 2문장 설명 (한국어)"
    }}
  ]
}}

[Newly Detected US SEC EDGAR Filings]
{sec_block}

[Newly Collected US News Articles]
{articles_block}
"""

    result = call_gemini_json(prompt, max_tokens=3500, temperature=0.1, thinking_budget=1024)
    if not result:
        return None

    # 1) SEC 공시 원본 정보 매핑 복원
    enriched_sec = []
    for s_item in result.get("sec_alerts", []):
        sid = s_item.get("sec_id")
        orig_s = sec_map.get(sid)
        if not orig_s:
            continue
        s_item["form"] = orig_s["form"]
        s_item["filing_date"] = orig_s["filing_date"]
        s_item["link"] = orig_s["link"]
        s_item["accession_number"] = orig_s["accession_number"]
        enriched_sec.append(s_item)
    result["sec_alerts"] = enriched_sec

    # 2) 뉴스 기사 원본 링크 및 언론사 매핑 복원
    enriched_news = []
    for item in result.get("selected_news", []):
        aid = item.get("article_id")
        orig = idx_map.get(aid)
        if not orig:
            continue
        item["publisher"] = orig.get("publisher", "US Media")
        item["link"] = orig.get("link", "")
        item["norm_key"] = orig.get("norm_key", "")
        enriched_news.append(item)
    result["selected_news"] = enriched_news

    return result


def build_briefing_message(profile: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    ticker = html.escape(profile.get("ticker", ""))
    company_name = html.escape(profile.get("company_name", ""))
    sentiment = html.escape(str(analysis.get("overall_sentiment") or ""))
    exec_summary = html.escape(str(analysis.get("executive_summary") or ""))

    lines = [
        f"🇺🇸 <b>[미국 주식 밸류체인 & SEC 레이더] {ticker}</b>",
        f"🏢 {company_name}",
        f"🧭 풍향계: <b>{sentiment}</b>",
        "",
        f"💡 <b>종합 요약</b>\n{exec_summary}",
    ]

    sec_alerts = analysis.get("sec_alerts", [])
    if sec_alerts:
        lines.extend(["", "🏛️ <b>미국 SEC EDGAR 공식 공시 알림</b>"])
        for j, sec in enumerate(sec_alerts[:5], 1):
            form = html.escape(str(sec.get("form") or ""))
            f_date = html.escape(str(sec.get("filing_date") or ""))
            sig = html.escape(str(sec.get("impact_signal") or ""))
            k_title = html.escape(str(sec.get("korean_title") or ""))
            s_desc = html.escape(str(sec.get("summary") or ""))
            s_link = sec.get("link", "")

            lines.append("")
            if s_link:
                lines.append(f'<b>[Form {form} | {sig}]</b> <a href="{s_link}">{k_title}</a> <i>({f_date})</i>')
                lines.append(f"└ <b>공시 내용</b>: {s_desc}")
                lines.append(f'└ 🔗 <a href="{s_link}">SEC EDGAR 원문 공시 보기</a>')
            else:
                lines.append(f"<b>[Form {form} | {sig}]</b> {k_title} <i>({f_date})</i>")
                lines.append(f"└ <b>공시 내용</b>: {s_desc}")

    selected_news = analysis.get("selected_news", [])
    if selected_news:
        lines.extend(["", "📰 <b>핵심 미국 뉴스 & 연결고리 분석</b>"])
        for i, item in enumerate(selected_news[:5], 1):
            badge = html.escape(str(item.get("layer_badge") or ""))
            signal = html.escape(str(item.get("impact_signal") or ""))
            headline = html.escape(str(item.get("korean_headline") or ""))
            why = html.escape(str(item.get("why_it_matters") or item.get("summary") or ""))
            pub = html.escape(str(item.get("publisher") or "US Media"))
            link = item.get("link", "")

            lines.append("")
            if link:
                lines.append(f'<b>{i}. [{badge} | {signal}]</b> <a href="{link}">{headline}</a>')
                lines.append(f"└ <b>{ticker} 연결고리</b>: {why}")
                lines.append(f'└ 🔗 <a href="{link}">미국 원문 기사 보기 ({pub})</a>')
            else:
                lines.append(f"<b>{i}. [{badge} | {signal}]</b> {headline} <i>({pub})</i>")
                lines.append(f"└ <b>{ticker} 연결고리</b>: {why}")

    cik = profile.get("sec_cik", "")
    sec_search_url = (
        f"https://www.sec.gov/edgar/browse/?CIK={cik}"
        if cik
        else f"https://www.sec.gov/edgar/searchedgar/companysearch"
    )
    lines.extend([
        "",
        f'📊 <a href="https://finance.yahoo.com/quote/{ticker}">Yahoo Finance</a> | '
        f'<a href="https://seekingalpha.com/symbol/{ticker}">Seeking Alpha</a> | '
        f'<a href="{sec_search_url}">SEC EDGAR ({ticker})</a>',
    ])

    return "\n".join(lines)


def send_telegram_message(message: str) -> bool:
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"❌ Telegram 발송 실패: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="미국 주식 밸류체인 & SEC 공시 인텔리전스 레이더")
    parser.add_argument("--dry-run", action="store_true", help="Telegram 발송 없이 콘솔에 브리핑 출력")
    parser.add_argument("--ticker", type=str, default=None, help="특정 티커만 일회성으로 테스트 (예: --ticker CRDO)")
    parser.add_argument("--refresh-profile", action="store_true", help="밸류체인 검색어 프로필을 새로 생성")
    parser.add_argument("--include-recent-sec", action="store_true", help="테스트용: 최근 30일 내 SEC 공시도 함께 표시")
    args = parser.parse_args()

    config = load_config()
    news_cfg = config.get("news", {})
    if not news_cfg.get("enabled", True) and not args.ticker:
        print("⏭️ 뉴스 브리핑이 비활성화되어 있습니다.")
        return

    telegram_ready = bool(
        TELEGRAM_TOKEN and not TELEGRAM_TOKEN.startswith("your-")
        and CHAT_ID and not CHAT_ID.startswith("your-")
    )
    if not args.dry_run and not telegram_ready:
        print("❌ TELEGRAM_TOKEN / CHAT_ID 미설정 (테스트 시 --dry-run 옵션을 사용하세요)")
        return

    tickers = [args.ticker.upper()] if args.ticker else news_cfg.get("us_tickers", ["CRDO"])
    lookback_days = news_cfg.get("lookback_days", 2)
    sec_lookback_days = 30 if args.include_recent_sec else news_cfg.get("sec_lookback_days", 7)
    max_per_layer = news_cfg.get("max_articles_per_layer", 8)
    profile_cache_file = Path(news_cfg.get("profile_cache_file", "us_stock_profiles.json"))
    sent_file = Path(news_cfg.get("sent_file", "news_sent.json"))
    max_history = news_cfg.get("max_history", 500)

    state = load_news_state(sent_file)
    seen_articles_set = set(state["seen_articles"])
    seen_sec_set = set(state["seen_sec_filings"])
    state_changed = False

    print(f"🚀 미국 주식 밸류체인 & SEC 레이더 시작 ({datetime.now(KST).isoformat()})")
    print(f"📍 감시 티커: {', '.join(tickers)}\n")

    for ticker in tickers:
        profile = get_or_build_ticker_profile(ticker, profile_cache_file, force_refresh=args.refresh_profile)
        if not profile:
            print(f"⚠️ [{ticker}] 프로필을 생성할 수 없어 건너뜁니다.")
            continue

        # 1) SEC EDGAR 공식 공시 체크
        print(f"🏛️ [{ticker}] 미국 SEC EDGAR 공식 공시(8-K, 10-Q/K, Form 4) 조회 중...")
        new_sec_filings, all_recent_sec = fetch_sec_edgar_filings(
            ticker,
            profile,
            seen_filings=seen_sec_set,
            lookback_days=sec_lookback_days,
        )
        # 최초 실행 시 과거 공시 폭탄 방지를 위해 최근 7일 이전 공시는 자동 시드 등록
        if not state["seen_sec_filings"] and all_recent_sec and not args.include_recent_sec:
            new_acc_set = {f["accession_number"] for f in new_sec_filings}
            for old_acc in all_recent_sec:
                if old_acc not in new_acc_set:
                    state["seen_sec_filings"].append(old_acc)
                    seen_sec_set.add(old_acc)
            state_changed = True

        print(f"   - 신규 SEC 공시: {len(new_sec_filings)}건")

        # 2) 3계층 미국 현지 영문 뉴스 수집
        print(f"🔍 [{ticker}] 3계층 미국 현지 뉴스 수집 중...")
        layered_articles, batch_article_keys = collect_layered_articles(
            profile,
            seen_keys=seen_articles_set,
            lookback_days=lookback_days,
            max_per_layer=max_per_layer,
        )
        total_news = sum(len(v) for v in layered_articles.values())
        print(f"   - 신규 미국 뉴스: 총 {total_news}건")

        if len(new_sec_filings) == 0 and total_news == 0:
            print(f"⏭️ [{ticker}] 지난 체크 이후 새로 올라온 SEC 공시나 미국 기사가 없습니다. (AI 호출 생략)\n")
            continue

        print(f"🤖 [{ticker}] Gemini SEC 공시 & 밸류체인 연결고리 분석 중...")
        analysis = analyze_intelligence_for_ticker(profile, new_sec_filings, layered_articles)
        if not analysis:
            print(f"⚠️ [{ticker}] AI 분석 실패 (다음 주기에 재시도합니다).\n")
            continue

        has_sec = bool(analysis.get("sec_alerts"))
        has_news = bool(analysis.get("selected_news"))

        # 평가가 정상 완료되었으므로, 이번에 검토한 모든 기사 키를 seen_articles에 기록 (1시간 뒤 탈락 기사 재평가 방지)
        if not args.dry_run:
            state["seen_articles"].extend(batch_article_keys)
            seen_articles_set.update(batch_article_keys)
            state_changed = True

        if not has_sec and not has_news:
            print(f"⏭️ [{ticker}] 신규 수집 기사가 모두 단순 클릭베이트/무의미한 소식으로 판별되어 알림을 생략합니다.\n")
            continue

        message = build_briefing_message(profile, analysis)

        if args.dry_run:
            print("\n" + "=" * 64)
            for line in message.splitlines():
                print(line)
            print("=" * 64 + "\n")
        elif send_telegram_message(message):
            print(f"✅ [{ticker}] 브리핑 발송 완료")
            for item in analysis.get("selected_news", []):
                if item.get("norm_key"):
                    state["sent_articles"].append(item["norm_key"])
            for s_item in analysis.get("sec_alerts", []):
                if s_item.get("accession_number"):
                    state["seen_sec_filings"].append(s_item["accession_number"])
                    seen_sec_set.add(s_item["accession_number"])
            state_changed = True
        else:
            print(f"❌ [{ticker}] 브리핑 발송 실패")

    if state_changed and not args.dry_run:
        save_news_state(sent_file, state, max_history=max_history)
        print(f"💾 이력 저장 완료 (검토 기사 {len(state['seen_articles'])}개, SEC 공시 {len(state['seen_sec_filings'])}개)")


if __name__ == "__main__":
    main()
