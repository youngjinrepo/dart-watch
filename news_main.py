"""
미국 주식 밸류체인 & 자본공급(CapEx) 인텔리전스 뉴스 브리핑 봇
- config.json의 news.us_tickers (예: ["CRDO"])에 티커만 넣으면:
  1) [AI 프로파일러] Gemini가 해당 기업의 밸류체인(핵심 사업, 고객사, 전방 CapEx 동인, 경쟁사)을 분석해
     3계층(Layer 1: 직접 언급, Layer 2: 전방산업/자본공급 CapEx, Layer 3: 생태계/경쟁사) 영문 검색 쿼리를 자동 생성 및 캐싱
  2) [미국 현지 뉴스 수집] Google News US (영문 RSS)에서 계층별 최신 미국 미디어(Bloomberg, CNBC, Reuters, Seeking Alpha 등) 기사 수집
  3) [2차 사고(Second-Order) 브리핑] 기사 본문에 티커가 직접 없더라도 전방 자본공급(CapEx) 둔화/확대나 경쟁사 동향이
     해당 종목에 미치는 연결고리와 투자 시사점을 한국어로 정리해 Telegram 발송
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
from typing import Any, Dict, List, Optional, Set

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


GEMINI_MODELS = [
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
]


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


def get_or_build_ticker_profile(ticker: str, cache_file: Path, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    """
    종목 티커(예: CRDO)에 대한 3계층 밸류체인 검색 프로필을 로드하거나,
    캐시에 없으면 Gemini를 통해 자동 생성하여 저장합니다.
    """
    ticker = ticker.upper().strip()
    profiles = load_json_file(cache_file, {})

    if not force_refresh and ticker in profiles:
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

            # 제목 끝의 ' - 언론사명' 분리 (HTML5 파서에서 <source>가 빈 태그로 처리될 때 폴백)
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
    sent_keys: Set[str],
    lookback_days: int = 2,
    max_per_layer: int = 8,
) -> Dict[str, List[Dict[str, str]]]:
    """3계층 쿼리를 순회하며 신규 미국 기사 수집 (중복 제거)"""
    queries_cfg = profile.get("queries", {})
    collected: Dict[str, List[Dict[str, str]]] = {
        "layer1_direct": [],
        "layer2_macro_capex": [],
        "layer3_ecosystem": [],
    }
    seen_titles: Set[str] = set()

    for layer_name in ["layer1_direct", "layer2_macro_capex", "layer3_ecosystem"]:
        q_list = queries_cfg.get(layer_name, [])
        layer_items: List[Dict[str, str]] = []
        for q in q_list:
            fetched = fetch_us_google_news(q, lookback_days=lookback_days, limit=max_per_layer)
            for art in fetched:
                norm_title = re.sub(r"\W+", "", art["title"].lower())[:60]
                if norm_title in seen_titles or norm_title in sent_keys:
                    continue
                seen_titles.add(norm_title)
                art["norm_key"] = norm_title
                layer_items.append(art)
                if len(layer_items) >= max_per_layer:
                    break
            if len(layer_items) >= max_per_layer:
                break
        collected[layer_name] = layer_items

    return collected


def analyze_articles_for_ticker(
    profile: Dict[str, Any],
    layered_articles: Dict[str, List[Dict[str, str]]],
) -> Optional[Dict[str, Any]]:
    """
    수집된 1~3계층 영문 기사들을 바탕으로, 종목(CRDO 등) 관점의 영향도와 연결고리를 분석
    """
    ticker = profile.get("ticker", "")
    company_name = profile.get("company_name", ticker)
    kr_summary = profile.get("korean_summary", "")
    products = ", ".join(profile.get("key_products_and_terms", []))
    customers = ", ".join(profile.get("key_customers", []))
    competitors = ", ".join(profile.get("key_competitors", []))
    risks = " / ".join(profile.get("hidden_risks_and_bottlenecks", []))

    # 프롬프트용 기사 목록 텍스트 구성
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

    if not idx_map:
        return None

    articles_block = "\n".join(article_lines)

    prompt = f"""You are a Senior Wall Street Equity Research Analyst.
Target Stock: {ticker} ({company_name})
- Business Summary: {kr_summary}
- Core Products & Niche Terms: {products}
- Key Customers / Buyers: {customers}
- Key Competitors / Peers: {competitors}
- Key Structural Risks & Bottlenecks to Watch: {risks}

Below is a list of recent US English news articles collected across 3 layers (1. Direct / 2. Downstream Budget & Bottlenecks / 3. Tech Ecosystem & Competitors).
Filter out generic clickbait and select up to 5 high-signal articles that a "{ticker}" shareholder MUST know.

CRITICAL SECOND-ORDER REASONING RULES:
1. Even if an article in Layer 2 or Layer 3 does NOT mention "{ticker}" by name, explain the exact second-order causal chain connecting that news to "{ticker}"'s future revenue, orders, margins, or valuation multiples.
2. Do NOT blindly label "record customer spending" as bullish (🟢 호재) if the article warns that customer spending is outpacing operating cash flow, relying on debt financing, facing ROI skepticism, or hitting physical/regulatory bottlenecks. Recognize those as **leading warning signals of future demand/budget slowdown** and classify them as `🟡 주의/체크` or `🔴 악재`.
3. Conduct your analytical reasoning in English, and write the final JSON values (`executive_summary`, `korean_headline`, `why_it_matters`) in natural, institutional-grade Korean.

Return ONLY valid JSON matching this schema:
{{
  "overall_sentiment": "🟢 호재 우세 | 🟡 혼조/관망 | 🔴 수요·업황 주의 중 택 1",
  "executive_summary": "{ticker} 투자자를 위한 오늘의 핵심 풍향계 2줄 종합 요약 (한국어)",
  "selected_news": [
    {{
      "article_id": 선택한 기사 번호(정수, 예: 1),
      "layer_badge": "🎯 직접 이슈 | 💰 전방 수요·자본 | 🔗 밸류체인·경쟁사 중 택 1",
      "impact_signal": "🟢 호재 | 🟡 주의/체크 | 🔴 악재 | ⚪ 중립 중 택 1",
      "korean_headline": "미국 기사 핵심 제목 한국어 번역",
      "why_it_matters": "{ticker} 주주 관점의 핵심 내용 및 2차 연결고리(인과관계) 2문장 이내 설명 (한국어)"
    }}
  ]
}}

[Collected US News Articles]
{articles_block}
"""

    result = call_gemini_json(prompt, max_tokens=2048, temperature=0.1, thinking_budget=1024)
    if not result:
        return None

    # 원본 기사 링크 및 언론사 매핑 복원
    enriched_news = []
    for item in result.get("selected_news", []):
        aid = item.get("article_id")
        orig = idx_map.get(aid, {})
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
        f"🇺🇸 <b>[미국 주식 밸류체인 레이더] {ticker}</b>",
        f"🏢 {company_name}",
        f"🧭 풍향계: <b>{sentiment}</b>",
        "",
        f"💡 <b>종합 요약</b>\n{exec_summary}",
        "",
        "📰 <b>핵심 시그널 & 연결고리 분석</b>",
    ]

    for i, item in enumerate(analysis.get("selected_news", [])[:5], 1):
        badge = html.escape(str(item.get("layer_badge") or ""))
        signal = html.escape(str(item.get("impact_signal") or ""))
        headline = html.escape(str(item.get("korean_headline") or ""))
        why = html.escape(str(item.get("why_it_matters") or ""))
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

    lines.extend([
        "",
        f'📊 <a href="https://finance.yahoo.com/quote/{ticker}">Yahoo Finance ({ticker})</a> | '
        f'<a href="https://seekingalpha.com/symbol/{ticker}">Seeking Alpha ({ticker})</a>',
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
    parser = argparse.ArgumentParser(description="미국 주식 밸류체인 & CapEx 인텔리전스 브리핑 봇")
    parser.add_argument("--dry-run", action="store_true", help="Telegram 발송 없이 콘솔에 브리핑 출력")
    parser.add_argument("--ticker", type=str, default=None, help="특정 티커만 일회성으로 테스트 (예: --ticker CRDO)")
    parser.add_argument("--refresh-profile", action="store_true", help="밸류체인 검색어 프로필을 새로 생성")
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
    max_per_layer = news_cfg.get("max_articles_per_layer", 8)
    profile_cache_file = Path(news_cfg.get("profile_cache_file", "us_stock_profiles.json"))
    sent_file = Path(news_cfg.get("sent_file", "news_sent.json"))
    max_history = news_cfg.get("max_history", 300)

    sent_list: List[str] = load_json_file(sent_file, [])
    if not isinstance(sent_list, list):
        sent_list = []
    sent_set = set(sent_list)
    newly_sent_keys: List[str] = []

    print(f"🚀 미국 주식 밸류체인 레이더 시작 ({datetime.now(KST).isoformat()})")
    print(f"📍 감시 티커: {', '.join(tickers)}\n")

    for ticker in tickers:
        profile = get_or_build_ticker_profile(ticker, profile_cache_file, force_refresh=args.refresh_profile)
        if not profile:
            print(f"⚠️ [{ticker}] 프로필을 생성할 수 없어 건너뜁니다.")
            continue

        print(f"🔍 [{ticker}] 3계층 미국 현지 뉴스 수집 중...")
        for layer_k, q_arr in profile.get("queries", {}).items():
            print(f"   - {layer_k}: {q_arr}")

        layered_articles = collect_layered_articles(
            profile,
            sent_keys=sent_set,
            lookback_days=lookback_days,
            max_per_layer=max_per_layer,
        )
        total_found = sum(len(v) for v in layered_articles.values())
        print(f"📊 [{ticker}] 수집된 신규 미국 기사: 총 {total_found}건")

        if total_found == 0:
            print(f"⏭️ [{ticker}] 새로운 미국 기사가 없습니다.\n")
            continue

        print(f"🤖 [{ticker}] Gemini 밸류체인 연결고리 & CapEx 시그널 분석 중...")
        analysis = analyze_articles_for_ticker(profile, layered_articles)
        if not analysis or not analysis.get("selected_news"):
            print(f"⚠️ [{ticker}] 유의미한 기사가 선별되지 않았습니다.\n")
            continue

        message = build_briefing_message(profile, analysis)

        if args.dry_run:
            print("\n" + "=" * 62)
            for line in message.splitlines():
                print(line)
            print("=" * 62 + "\n")
        elif send_telegram_message(message):
            print(f"✅ [{ticker}] 브리핑 발송 완료")
            for item in analysis.get("selected_news", []):
                if item.get("norm_key"):
                    newly_sent_keys.append(item["norm_key"])
        else:
            print(f"❌ [{ticker}] 브리핑 발송 실패")

    if newly_sent_keys and not args.dry_run:
        updated = list(dict.fromkeys(sent_list + newly_sent_keys))[-max_history:]
        save_json_file(sent_file, updated)
        print(f"💾 뉴스 발송 이력 저장 완료 ({len(updated)}개)")


if __name__ == "__main__":
    main()
