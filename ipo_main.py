"""
AI 공모주(IPO) 100점 스코어링 분석 및 알림 봇
- DART 발행공시(C001, 증권신고서-지분증권, corp_cls=E) 감지
- [발행조건확정]증권신고서 원문 + 최신 정정신고서/투자설명서 원문을 결합해 자료 완성도 극대화
- AI(Gemini)는 원문에서 객관적 수치 및 정성 루브릭 근거만 추출하고,
  파이썬 스코어링 엔진이 100점 만점 기준표·복합 위험 등급 상한(Cap)·자료 완성도를 엄격히 계산
- 2단계 알림 타이밍 지원:
  1) 기관 수요예측 결과 발표([발행조건확정]) 즉시 100점 스코어링 리포트 발송
  2) 일반청약 1일차 당일 리마인더 브리핑 발송
"""

import os
import io
import re
import time
import html
import json
import zipfile
import argparse
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

KST = timezone(timedelta(hours=9))

load_dotenv()

DART_API_KEY = os.getenv("DART_API_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

CONFIG_FILE = Path("config.json")

GRADE_ORDER = ["A", "B", "C", "D", "E"]
GRADE_LABELS = {
    "A": "적극 청약 검토",
    "B": "청약 긍정 검토",
    "C": "청약 관망",
    "D": "청약 비추천",
    "E": "청약 회피",
}


def load_config() -> Dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_ipo_state(state_file: Path) -> Dict[str, Any]:
    """
    발송 이력, 청약 1일차 리마인더 대기 목록, 주간 캘린더 발송일 로드
    하위 호환성: 기존 리스트 형태면 딕셔너리 형태로 변환
    """
    default_state = {
        "sent_rcept_nos": [],
        "scheduled_reminders": {},
        "last_weekly_calendar_date": "",
    }
    if not state_file.exists():
        return default_state
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return {
                    "sent_rcept_nos": [str(x) for x in data],
                    "scheduled_reminders": {},
                    "last_weekly_calendar_date": "",
                }
            if isinstance(data, dict):
                return {
                    "sent_rcept_nos": [str(x) for x in data.get("sent_rcept_nos", [])],
                    "scheduled_reminders": data.get("scheduled_reminders", {}),
                    "last_weekly_calendar_date": str(data.get("last_weekly_calendar_date", "")),
                }
    except Exception:
        pass
    return default_state


def save_ipo_state(state_file: Path, state: Dict[str, Any], max_history: int = 100) -> None:
    unique_rcepts = list(dict.fromkeys(state.get("sent_rcept_nos", [])))
    state["sent_rcept_nos"] = unique_rcepts[-max_history:]

    today_kst = datetime.now(KST).date()
    cleaned_reminders: Dict[str, Any] = {}
    for key_id, info in (state.get("scheduled_reminders") or {}).items():
        if not isinstance(info, dict):
            continue
        sub_str = str(info.get("subscription_start_date") or "").strip()
        try:
            sub_date = datetime.strptime(sub_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        # 청약 시작일이 오늘보다 과거인 리마인더는 발송 여부와 무관하게 즉시 정리
        if sub_date < today_kst:
            continue
        cleaned_reminders[key_id] = info

    state["scheduled_reminders"] = cleaned_reminders
    state["last_weekly_calendar_date"] = str(state.get("last_weekly_calendar_date") or "")
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_ipo_filings(days: int = 3, only_confirmed: bool = True, exclude_spac: bool = True) -> List[Dict]:
    """DART API에서 신규 공모주(corp_cls='E', pblntf_detail_ty='C001') 증권신고서(지분증권) 조회"""
    today = datetime.now(KST)
    start_date = today - timedelta(days=days)
    bgn_de = start_date.strftime("%Y%m%d")
    end_de = today.strftime("%Y%m%d")

    print(f"📅 IPO 공시 조회 기간 (KST): {bgn_de} ~ {end_de}")

    url = "https://opendart.fss.or.kr/api/list.json"
    all_items: List[Dict] = []
    page_no = 1

    while page_no <= 10:
        params = {
            "crtfc_key": DART_API_KEY,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "corp_cls": "E",
            "pblntf_detail_ty": "C001",
            "page_no": page_no,
            "page_count": 100,
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "000":
            break

        items = data.get("list", [])
        all_items.extend(items)

        if page_no >= data.get("total_page", 1) or not items:
            break
        page_no += 1

    filtered: List[Dict] = []
    for item in all_items:
        if item.get("corp_cls") != "E":
            continue

        report_nm = re.sub(r"\s+", " ", item.get("report_nm", "")).strip()
        corp_name = item.get("corp_name", "").strip()

        if "지분증권" not in report_nm:
            continue

        if exclude_spac and ("기업인수목적" in corp_name or "스팩" in corp_name):
            continue

        if only_confirmed and "[발행조건확정]" not in report_nm:
            continue

        filtered.append(item)

    print(f"📊 조회된 발행공시 {len(all_items)}건 중 분석 대상 공모주 공시: {len(filtered)}건")
    return filtered


def fetch_ipo_withdrawals(days: int = 3, exclude_spac: bool = True) -> List[Dict]:
    """최근 공시 중 지분증권(공모주) 관련 철회신고서 조회"""
    today = datetime.now(KST)
    bgn_de = (today - timedelta(days=days)).strftime("%Y%m%d")
    end_de = today.strftime("%Y%m%d")
    url = "https://opendart.fss.or.kr/api/list.json"

    withdrawals: List[Dict] = []
    page_no = 1
    while page_no <= 5:
        try:
            resp = requests.get(
                url,
                params={
                    "crtfc_key": DART_API_KEY,
                    "bgn_de": bgn_de,
                    "end_de": end_de,
                    "corp_cls": "E",
                    "pblntf_ty": "C",
                    "page_no": page_no,
                    "page_count": 100,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "000":
                break
            items = data.get("list", [])
            for item in items:
                report_nm = re.sub(r"\s+", " ", item.get("report_nm", "")).strip()
                corp_name = item.get("corp_name", "").strip()
                if "철회신고서" not in report_nm:
                    continue
                if exclude_spac and ("기업인수목적" in corp_name or "스팩" in corp_name):
                    continue
                withdrawals.append(item)
            if page_no >= data.get("total_page", 1) or not items:
                break
            page_no += 1
        except Exception as e:
            print(f"⚠️ 철회신고서 조회 실패: {e}")
            break

    # 해당 기업이 최근 90일 내 지분증권 공모 신고서를 제출했던 IPO 대상인지 확인
    confirmed_withdrawals: List[Dict] = []
    lookback_90 = (today - timedelta(days=90)).strftime("%Y%m%d")
    for w in withdrawals:
        cc = w.get("corp_code", "").strip()
        if not cc:
            continue
        try:
            resp = requests.get(
                url,
                params={
                    "crtfc_key": DART_API_KEY,
                    "corp_code": cc,
                    "bgn_de": lookback_90,
                    "end_de": end_de,
                    "pblntf_detail_ty": "C001",
                    "page_count": 20,
                },
                timeout=10,
            )
            resp.raise_for_status()
            c_items = resp.json().get("list", [])
            if any("지분증권" in x.get("report_nm", "") for x in c_items):
                confirmed_withdrawals.append(w)
        except Exception:
            continue
    return confirmed_withdrawals


def _download_dart_xml_text(rcept_no: str) -> Optional[str]:
    """rcept_no로 DART document.xml ZIP을 내려받아 전체 텍스트 반환"""
    doc_url = "https://opendart.fss.or.kr/api/document.xml"
    try:
        resp = requests.get(
            doc_url,
            params={"crtfc_key": DART_API_KEY, "rcept_no": rcept_no},
            timeout=25,
        )
        resp.raise_for_status()
        if resp.content[:2] != b"PK":
            return None

        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            xml_names = [n for n in z.namelist() if n.lower().endswith((".xml", ".html", ".htm"))]
            target_name = max(xml_names, key=lambda n: z.getinfo(n).file_size) if xml_names else z.namelist()[0]
            raw_bytes = z.read(target_name)

        soup = BeautifulSoup(raw_bytes, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except Exception as e:
        print(f"⚠️ DART 문서 다운로드 실패 ({rcept_no}): {e}")
        return None


def _find_companion_full_doc_text(corp_code: str, current_rcept_no: str) -> Optional[str]:
    """
    [발행조건확정] 정정신고서에 재무제표나 유통가능물량 표가 생략된 경우,
    동일 기업의 가장 최근 투자설명서 또는 증권신고서 본문을 찾아 텍스트 반환
    """
    if not corp_code:
        return None
    try:
        today = datetime.now(KST)
        bgn_de = (today - timedelta(days=180)).strftime("%Y%m%d")
        end_de = today.strftime("%Y%m%d")
        resp = requests.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={
                "crtfc_key": DART_API_KEY,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "pblntf_ty": "C",
                "page_count": 30,
            },
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("list", [])
        for item in items:
            rno = item.get("rcept_no", "")
            rnm = item.get("report_nm", "")
            if rno == current_rcept_no:
                continue
            if "투자설명서" in rnm or "증권신고서(지분증권)" in rnm:
                text = _download_dart_xml_text(rno)
                if text and len(text) > 150000:
                    return text
    except Exception:
        pass
    return None


def _slice_around(text: str, pattern: str, before: int = 200, after: int = 3500, use_last: bool = False) -> Optional[str]:
    matches = list(re.finditer(pattern, text))
    if not matches:
        return None
    target = matches[-1] if use_last else matches[0]
    start = max(0, target.start() - before)
    end = min(len(text), target.start() + after)
    return text[start:end]


def extract_ipo_sections(rcept_no: str, corp_code: str = "") -> Optional[str]:
    """
    7대 스코어링 항목(기업실적, 밸류에이션, 유통물량, 경쟁률, 가격분포, 의무보유확약, 공모구조/자금사용처)에
    필요한 모든 섹션을 원문(및 보완 본문)에서 스마트 발췌
    """
    main_text = _download_dart_xml_text(rcept_no)
    if not main_text:
        return None

    # 정정신고서([발행조건확정])는 정정된 부분만 담고 있는 경우가 많으므로 본 투자설명서/증권신고서도 확보
    companion_text = None
    if len(main_text) < 250000 and corp_code:
        companion_text = _find_companion_full_doc_text(corp_code, rcept_no)
    full_source = companion_text or main_text

    chunks: List[str] = []

    # 1) 표지 및 공통 정정사항 (희망공모가 밴드, 확정공모가, 공모주식수 등)
    chunks.append("=== [1. 문서 상단 및 정정사항 요약] ===\n" + main_text[:3500])

    # 2) 공모개요 및 공모방법 (신주모집 vs 구주매출 비율, 대표주관사, 청약기일)
    overview_matches = list(re.finditer(r"1\.\s*공모개요", main_text))
    if overview_matches:
        pos = overview_matches[1].start() if len(overview_matches) >= 2 else overview_matches[0].start()
        chunks.append("=== [2. 공모개요 및 공모방법 (신주모집/구주매출)] ===\n" + main_text[pos : pos + 3500])

    # 3) 기관 수요예측 결과 (참여내역 경쟁률, 신청가격 분포, 의무보유 확약기간별 내역)
    demand_slice = _slice_around(main_text, r"수요예측\s*참여\s*내역", before=400, after=7500)
    if not demand_slice:
        demand_slice = _slice_around(main_text, r"수요예측\s*결과", before=200, after=6000, use_last=True)
    if demand_slice:
        chunks.append("=== [3. 기관 수요예측 경쟁률·가격분포·의무보유확약] ===\n" + demand_slice)

    # 4) 인수인의 의견 / 주당 평가가액 산출 (비교기업 PER/EV·EBITDA 배수, 평가액 대비 할인율)
    val_body = main_text[3000:] if len(main_text) > 5000 else main_text
    val_slice = _slice_around(val_body, r"(할인율|주당\s*평가가액|유사회사|비교기업|EV/EBITDA)", before=800, after=7500, use_last=False)
    if val_slice:
        chunks.append("=== [4. 밸류에이션 (비교기업 배수·주당평가가액·할인율)] ===\n" + val_slice)

    # 5) 자금의 사용목적 (시설투자, R&D, 운영자금, 차입금상환 등)
    fund_slice = _slice_around(main_text, r"자금의\s*사용목적", before=100, after=3000, use_last=True)
    if fund_slice:
        chunks.append("=== [5. 공모자금의 사용목적] ===\n" + fund_slice)

    # 6) 상장 직후 유통가능 물량 및 보호예수
    tradable_slice = _slice_around(
        main_text,
        r"(상장\s*이후\s*유통물량|유통제한\s*및\s*유통가능|유통가능한\s*물량|매도물량\s*출회)",
        before=300,
        after=5000,
        use_last=True,
    )
    if (not tradable_slice or len(tradable_slice) < 800) and companion_text:
        comp_trad = _slice_around(
            companion_text,
            r"(상장\s*이후\s*유통물량|유통제한\s*및\s*유통가능|유통가능한\s*물량|매도물량\s*출회|유통가능\s*주식)",
            before=300,
            after=5000,
            use_last=False,
        )
        if comp_trad:
            tradable_slice = (tradable_slice or "") + "\n" + comp_trad
    if tradable_slice:
        chunks.append("=== [6. 상장 직후 유통가능 물량 및 보호예수] ===\n" + tradable_slice)

    # 7) 요약재무정보 (매출·영업이익·부채) 및 현금흐름표
    fin_slice = _slice_around(full_source, r"(요약재무정보|요약연결재무정보)", before=100, after=4000, use_last=False)
    if not fin_slice:
        fin_slice = _slice_around(full_source, r"매출액", before=300, after=3500, use_last=False)
    cf_slice = _slice_around(full_source, r"(영업활동\s*현금흐름|영업활동으로\s*인한\s*현금흐름|현금흐름표)", before=200, after=2500, use_last=False)
    if fin_slice or cf_slice:
        chunks.append("=== [7. 재무실적 및 현금흐름] ===\n" + (fin_slice or "") + "\n" + (cf_slice or ""))

    # 8) 사업 및 고객사 집중 위험 / 핵심투자위험
    risk_slice = _slice_around(full_source, r"핵심투자위험", before=100, after=4000, use_last=False)
    cust_slice = _slice_around(full_source, r"(주요\s*매출처|고객사|매출처\s*집중|매출\s*의존|의존도)", before=200, after=2500, use_last=False)
    if risk_slice or cust_slice:
        chunks.append("=== [8. 주요 투자위험 및 고객사 집중도] ===\n" + (risk_slice or "") + "\n" + (cust_slice or ""))

    return "\n\n".join(chunks)


def analyze_ipo_with_gemini(extracted_text: str, corp_name: str, report_nm: str) -> Optional[Dict]:
    """
    Gemini API를 호출해 100점 스코어링 기준표에 필요한 정량 수치 + 정성 루브릭 점수를 JSON으로 추출
    """
    if not GEMINI_API_KEY or GEMINI_API_KEY.startswith("your-"):
        print("⚠️ GEMINI_API_KEY 미설정")
        return None

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

    prompt = f"""당신은 한국 IPO(공모주) 공시 데이터를 분석하는 정밀 스코어링 AI입니다.
다음 '{corp_name}'의 '{report_nm}' DART 공식 원문 발췌본을 읽고 아래 규칙에 따라 JSON을 반환하세요.

[절대 준수 규칙]
1. 공식 문서에서 확인되지 않는 미공개 수치는 절대 추측하지 말고 null로 기재하세요.
2. 모든 비율(%)과 배수, 금액은 단위 기호 없이 숫자(float 또는 int)로만 기재하세요.
3. 기관 가격 제시 분포(price_at_or_above_confirmed_ratio_pct)는 반드시 '기관 수'가 아닌 '신청 수량' 기준으로 최종 확정공모가 이상 가격을 제시한 수량의 비율(%)을 기재하세요. '가격 미제시' 물량은 최종 공모가 이상 수요에 포함하지 마세요.
4. 의무보유확약 비율(lockup_ratio_pct)은 '기관 신청 수량 합계' 대비 '확약 신청 수량(6개월+3개월+1개월+15일 합계, 즉 합계 - 미확약)'의 비율(%)로 계산하세요.
5. 장기 확약 비중(long_term_lockup_share_pct)은 '전체 의무보유확약 신청 수량' 중에서 '3개월 확약 + 6개월 확약 수량'이 차지하는 비중(%)입니다. (확약 수량이 0이면 0.0)
6. 신주모집 비율(new_share_ratio_pct)은 전체 공모주식수 중 신주모집 주식수의 비율(%)입니다. (전량 신주모집이면 100.0)
7. 정성/반정량 평가 점수는 아래 기준표 배점 내에서만 부여하고, 원문에서 전혀 판단할 수 없으면 null을 기재하세요:
   - rev_profit_growth_score (0~6점): 지속적 매출 성장+영업이익 증가(6), 성장하나 실적 변동성 존재(4), 최근 흑자전환 또는 성장 둔화(2), 적자 지속/기술특례 적자 및 악화(0~1)
   - cashflow_health_score (0~4점): 영업현금흐름 지속 흑자+부채 안정(4), 혼재하나 무난(3), 순이익·현금흐름 괴리 또는 부채 증가(2), 현금흐름 악화 및 유동성 위험(0~1)
   - customer_diversification_score (0~5점): 고객·제품 다변화/최대고객 20% 미만(5), 최대고객 20~30%(4), 30~50%(3), 50~70%(2), 70~80%(1), 80% 이상(0)
   - peer_multiple_score (0~12점): 비교기업 대비 평가배수 적정성. 확정공모가 기준 비교기업 중앙 배수 대비 75% 이하(12), 75~90%(10), 90~100%(8), 100~115%(6), 115~130%(3), 130% 초과(0)
   - valuation_basis_score (0~5점): 실제 확정 실적 중심(5), 일부 정상화·추정치 반영(4), 반기/3분기 실적 연환산(3), 미래 추정 실적(연평균 할인 등) 의존(2), 근거 약한 장기 미래 추정 실적 의존(0~1)
   - fund_usage_score (0~5점): 구체적인 시설투자·R&D 및 성장계획(5), 구체적 설비투자 및 사업확장(4), 운영자금·차입금 상환 등 혼합(3), 사용 목적 포괄적/불명확(1~2), 회사로 신규 자금 유입 없음(0)

반환할 JSON 스키마:
{{
  "corp_name": "{corp_name}",
  "market": "코스닥 또는 유가증권",
  "lead_manager": "대표주관회사명",
  "subscription_date": "YYYY.MM.DD ~ YYYY.MM.DD 형태의 일반청약일",
  "subscription_start_date": "YYYY-MM-DD 형태의 일반청약 1일차 날짜",
  "payment_date": "YYYY.MM.DD 형태의 납입기일",
  "band_low": 희망공모가 하단(원, 정수),
  "band_high": 희망공모가 상단(원, 정수),
  "confirmed_price": 최종 확정공모가(원, 정수),
  "evaluated_price_per_share": 주당 평가가액(원, 정수 또는 null),
  "discount_rate_pct": 산정 주당평가가액 대비 확정공모가 할인율(%, float 또는 null, 할증이면 음수),
  "is_overvalued": 평가액 자체가 과대평가(미래 추정치 과다 또는 희망밴드 상단 초과에도 고평가)되었는지 여부(true/false),
  "tradable_ratio_pct": 상장 직후 유통가능 물량 비율(%, float 또는 null),
  "tradable_shares": 상장 직후 유통가능 주식수(주, 정수 또는 null),
  "tradable_amount_eok": 확정공모가 기준 상장 직후 유통가능 금액(억 원 단위 float, 확인불가시 null),
  "competition_rate": 기관 수요예측 경쟁률(:1 제외한 float, 예: 1051.38),
  "price_at_or_above_confirmed_ratio_pct": 최종 공모가 이상 가격 제시 신청 수량 비율(%, 가격미제시 제외, float 또는 null),
  "unpriced_ratio_pct": 가격 미제시 신청 수량 비율(%, float 또는 null),
  "lockup_ratio_pct": 기관 신청 수량 기준 의무보유확약 비율(%, float 또는 null),
  "long_term_lockup_share_pct": 전체 의무보유확약 수량 중 3개월+6개월 확약 수량 비중(%, float 또는 null),
  "new_share_ratio_pct": 신주모집 비율(%, 전량 신주모집이면 100.0, float 또는 null),
  "rev_profit_growth_score": 0~6 사이 정수 또는 null,
  "cashflow_health_score": 0~4 사이 정수 또는 null,
  "customer_diversification_score": 0~5 사이 정수 또는 null,
  "peer_multiple_score": 0~12 사이 정수 또는 null,
  "valuation_basis_score": 0~5 사이 정수 또는 null,
  "fund_usage_score": 0~5 사이 정수 또는 null,
  "audit_or_accounting_risk": 감사의견 거절·부적정·계속기업 불확실성·중대한 회계오류 여부(true/false),
  "customer_concentration_over_80": 최대 고객사 매출 비중 80% 초과 여부(true/false),
  "earnings_deteriorating": 최근 실적 악화 또는 적자 심화 여부(true/false),
  "positive_factors": ["긍정 요소 1 (간결한 한 줄)", "긍정 요소 2", "긍정 요소 3"],
  "risk_factors": ["위험 요소 1 (간결한 한 줄)", "위험 요소 2", "위험 요소 3"],
  "final_comment": "데이터에 기반한 종합 판단 한 줄 코멘트"
}}

[DART 공식 원문 발췌]
{extracted_text}
"""

    models = ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-3.1-flash-lite"]
    for model_name in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        gen_cfg: Dict[str, Any] = {
            "maxOutputTokens": 2048,
            "temperature": 0.0,
            "responseMimeType": "application/json",
        }
        if "flash-lite" not in model_name:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
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
                    timeout=35,
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
                raw_json = "\n".join(p.get("text", "") for p in parts if not p.get("thought") and "text" in p).strip()
                parsed = json.loads(raw_json)
                if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                    parsed = parsed[0]
                if not isinstance(parsed, dict):
                    raise ValueError("Expected JSON object")
                return parsed
            except Exception as e:
                if attempt < 2:
                    time.sleep(2)
                    continue
                print(f"⚠️ [{model_name}] IPO 분석 실패 ({e.__class__.__name__})")
    return None


def score_ipo_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    100점 만점 AI 공모주 스코어링 기준표에 따른 결정론적(Deterministic) 점수 및 위험 규칙 계산
    """
    categories: Dict[str, Dict[str, Any]] = {}

    # ① 기업 실적 및 사업 경쟁력 (15점: 성장 6 + 현금흐름 4 + 고객다변화 5)
    c1_earned = 0
    c1_max = 0
    for field, max_pt in [
        ("rev_profit_growth_score", 6),
        ("cashflow_health_score", 4),
        ("customer_diversification_score", 5),
    ]:
        val = data.get(field)
        if isinstance(val, (int, float)):
            c1_earned += max(0, min(max_pt, int(round(val))))
            c1_max += max_pt
    categories["기업 경쟁력"] = {"earned": c1_earned, "max": c1_max, "full_max": 15}

    # ② 공모가 및 밸류에이션 (20점: 비교기업 배수 12 + 평가근거 신뢰성 5 + 할인율 3)
    c2_earned = 0
    c2_max = 0
    peer_sc = data.get("peer_multiple_score")
    if isinstance(peer_sc, (int, float)):
        c2_earned += max(0, min(12, int(round(peer_sc))))
        c2_max += 12

    basis_sc = data.get("valuation_basis_score")
    if isinstance(basis_sc, (int, float)):
        c2_earned += max(0, min(5, int(round(basis_sc))))
        c2_max += 5

    disc = data.get("discount_rate_pct")
    # 확정공모가와 평가가액이 있으면 직접 할인율 재검증
    eval_price = data.get("evaluated_price_per_share")
    conf_price = data.get("confirmed_price")
    if isinstance(eval_price, (int, float)) and eval_price > 0 and isinstance(conf_price, (int, float)):
        disc = (1.0 - (conf_price / eval_price)) * 100.0

    if isinstance(disc, (int, float)):
        c2_max += 3
        if data.get("is_overvalued"):
            # 할인율이 높더라도 평가액 자체가 과대평가됐으면 긍정적으로 판단하지 않음 (최대 1점 제한)
            disc_pt = 1 if disc >= 20 else 0
        else:
            if disc >= 30:
                disc_pt = 3
            elif disc >= 20:
                disc_pt = 2
            elif disc >= 10:
                disc_pt = 1
            else:
                disc_pt = 0
        c2_earned += disc_pt
    categories["밸류에이션"] = {"earned": c2_earned, "max": c2_max, "full_max": 20}

    # ③ 상장 직후 유통가능 물량 (20점: 유통가능 비율 15 + 유통가능 금액 5)
    c3_earned = 0
    c3_max = 0
    trad_ratio = data.get("tradable_ratio_pct")
    if isinstance(trad_ratio, (int, float)):
        c3_max += 15
        if trad_ratio < 15:
            c3_earned += 15
        elif trad_ratio < 20:
            c3_earned += 13
        elif trad_ratio < 25:
            c3_earned += 11
        elif trad_ratio < 30:
            c3_earned += 9
        elif trad_ratio < 35:
            c3_earned += 7
        elif trad_ratio < 40:
            c3_earned += 4
        elif trad_ratio <= 45:
            c3_earned += 2
        else:
            c3_earned += 0

    trad_shares = data.get("tradable_shares")
    trad_amt = data.get("tradable_amount_eok")
    if isinstance(trad_shares, (int, float)) and trad_shares > 0 and isinstance(conf_price, (int, float)) and conf_price > 0:
        trad_amt = (trad_shares * conf_price) / 100_000_000.0
        data["tradable_amount_eok"] = trad_amt
    if isinstance(trad_amt, (int, float)):
        c3_max += 5
        if trad_amt < 300:
            c3_earned += 5
        elif trad_amt < 700:
            c3_earned += 4
        elif trad_amt < 1500:
            c3_earned += 3
        elif trad_amt < 3000:
            c3_earned += 2
        elif trad_amt <= 5000:
            c3_earned += 1
        else:
            c3_earned += 0
    categories["유통가능 물량"] = {"earned": c3_earned, "max": c3_max, "full_max": 20}

    # ④ 기관 수요예측 경쟁률 (5점)
    c4_earned = 0
    c4_max = 0
    comp = data.get("competition_rate")
    price_dist = data.get("price_at_or_above_confirmed_ratio_pct")
    if isinstance(comp, (int, float)):
        c4_max += 5
        if comp < 100:
            c4_pt = 0
        elif comp < 200:
            c4_pt = 1
        elif comp < 500:
            c4_pt = 2
        elif comp < 1000:
            c4_pt = 4
        else:
            c4_pt = 5
        # 지침: 경쟁률이 높더라도 가격 제시 분포가 낮으면(<50%) 긍정적으로 평가하지 않음
        if isinstance(price_dist, (int, float)) and price_dist < 50:
            c4_pt = min(c4_pt, 2)
        c4_earned += c4_pt
    categories["기관 경쟁률"] = {"earned": c4_earned, "max": c4_max, "full_max": 5}

    # ⑤ 기관 가격 제시 분포 (15점)
    c5_earned = 0
    c5_max = 0
    if isinstance(price_dist, (int, float)):
        c5_max += 15
        if price_dist >= 90:
            c5_earned += 15
        elif price_dist >= 80:
            c5_earned += 13
        elif price_dist >= 70:
            c5_earned += 11
        elif price_dist >= 60:
            c5_earned += 9
        elif price_dist >= 50:
            c5_earned += 7
        elif price_dist >= 40:
            c5_earned += 5
        elif price_dist >= 20:
            c5_earned += 2
        else:
            c5_earned += 0
    categories["가격 제시 분포"] = {"earned": c5_earned, "max": c5_max, "full_max": 15}

    # ⑥ 기관 의무보유확약 (15점: 확약 비율 12 + 장기 확약 비중 3)
    c6_earned = 0
    c6_max = 0
    lockup = data.get("lockup_ratio_pct")
    if isinstance(lockup, (int, float)):
        c6_max += 12
        if lockup < 5:
            c6_earned += 0
        elif lockup < 10:
            c6_earned += 2
        elif lockup < 20:
            c6_earned += 4
        elif lockup < 30:
            c6_earned += 6
        elif lockup < 40:
            c6_earned += 8
        elif lockup < 50:
            c6_earned += 10
        else:
            c6_earned += 12

    lt_lockup = data.get("long_term_lockup_share_pct")
    if isinstance(lt_lockup, (int, float)):
        c6_max += 3
        if lt_lockup < 10:
            c6_earned += 0
        elif lt_lockup < 30:
            c6_earned += 1
        elif lt_lockup < 50:
            c6_earned += 2
        else:
            c6_earned += 3
    categories["의무보유확약"] = {"earned": c6_earned, "max": c6_max, "full_max": 15}

    # ⑦ 공모 구조 및 자금 사용처 (10점: 신주모집 비율 5 + 사용처 5)
    c7_earned = 0
    c7_max = 0
    new_sh = data.get("new_share_ratio_pct")
    if isinstance(new_sh, (int, float)):
        c7_max += 5
        if new_sh >= 90:
            c7_earned += 5
        elif new_sh >= 70:
            c7_earned += 4
        elif new_sh >= 50:
            c7_earned += 3
        elif new_sh >= 30:
            c7_earned += 2
        elif new_sh >= 1:
            c7_earned += 1
        else:
            c7_earned += 0

    fund_sc = data.get("fund_usage_score")
    if isinstance(fund_sc, (int, float)):
        c7_earned += max(0, min(5, int(round(fund_sc))))
        c7_max += 5
    categories["공모 구조"] = {"earned": c7_earned, "max": c7_max, "full_max": 10}

    # 총점 및 자료 완성도 산출
    raw_earned = sum(c["earned"] for c in categories.values())
    available_max = sum(c["max"] for c in categories.values())
    completeness_pct = int(round((available_max / 100.0) * 100))

    if available_max > 0:
        normalized_score = int(round((raw_earned / available_max) * 100))
    else:
        normalized_score = 0

    # 기본 등급 산출
    if normalized_score >= 85:
        base_grade = "A"
    elif normalized_score >= 70:
        base_grade = "B"
    elif normalized_score >= 55:
        base_grade = "C"
    elif normalized_score >= 40:
        base_grade = "D"
    else:
        base_grade = "E"

    # 위험 판정 규칙 및 복합 위험 등급 상한(Cap) 적용
    warnings_triggered: List[str] = []
    cap_grade = "A"

    def apply_cap(limit_grade: str):
        nonlocal cap_grade
        if GRADE_ORDER.index(limit_grade) > GRADE_ORDER.index(cap_grade):
            cap_grade = limit_grade

    if data.get("audit_or_accounting_risk"):
        warnings_triggered.append("중대한 회계·감사 위험 감지 (등급 상한 E)")
        apply_cap("E")

    if data.get("customer_concentration_over_80"):
        warnings_triggered.append("최대 고객사 매출 비중 80% 초과 경고")
        if data.get("earnings_deteriorating"):
            warnings_triggered.append("고객사 집중 80% 초과 + 실적 악화 (등급 상한 D)")
            apply_cap("D")

    if isinstance(trad_ratio, (int, float)) and trad_ratio > 45:
        warnings_triggered.append(f"상장 직후 유통가능 비율 45% 초과 ({trad_ratio:.1f}%) 수급 경고")

    if isinstance(lockup, (int, float)) and lockup < 5:
        warnings_triggered.append(f"기관 의무보유확약률 5% 미만 ({lockup:.2f}%, 잠정·신청수량 기준)")

    if isinstance(price_dist, (int, float)) and price_dist < 20:
        warnings_triggered.append(f"최종 공모가 이상 가격 제시 분포 20% 미만 ({price_dist:.1f}%) 경고")

    # 복합 위험 조합 체크
    if isinstance(trad_ratio, (int, float)) and trad_ratio > 40 and isinstance(lockup, (int, float)) and lockup < 5:
        warnings_triggered.append("복합위험: 유통가능 40% 초과 + 확약률 5% 미만 (등급 상한 C)")
        apply_cap("C")

    if isinstance(price_dist, (int, float)) and price_dist < 30 and isinstance(lockup, (int, float)) and lockup < 5:
        warnings_triggered.append("복합위험: 가격 제시 분포 30% 미만 + 확약률 5% 미만 (등급 상한 C)")
        apply_cap("C")

    is_high_val = bool(data.get("is_overvalued")) or (c2_max > 0 and (c2_earned / c2_max) < 0.4)
    if is_high_val and isinstance(comp, (int, float)) and comp < 100 and isinstance(lockup, (int, float)) and lockup < 5:
        warnings_triggered.append("복합위험: 고평가 + 경쟁률 100:1 미만 + 낮은 확약률 (등급 상한 D)")
        apply_cap("D")

    final_grade = base_grade
    if GRADE_ORDER.index(cap_grade) > GRADE_ORDER.index(base_grade):
        final_grade = cap_grade

    if completeness_pct < 80:
        verdict_label = "분석 보류 (자료 완성도 80% 미만)"
    else:
        verdict_label = f"{GRADE_LABELS[final_grade]} ({final_grade}등급)"
        if final_grade != base_grade:
            verdict_label += f" [원등급 {base_grade}→위험상한 {final_grade}]"

    return {
        "categories": categories,
        "raw_earned": raw_earned,
        "available_max": available_max,
        "completeness_pct": completeness_pct,
        "normalized_score": normalized_score,
        "base_grade": base_grade,
        "final_grade": final_grade,
        "verdict_label": verdict_label,
        "warnings_triggered": warnings_triggered,
    }


def build_ipo_message(filing: Dict, data: Optional[Dict], score_result: Optional[Dict]) -> str:
    """# 5. 실제 사용자에게 전송할 메시지 형식에 맞춘 텔레그램 메시지 생성"""
    corp_name = html.escape(filing.get("corp_name", "Unknown").strip())
    rcept_no = filing.get("rcept_no", "").strip()
    dart_link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

    if not data or not score_result:
        return (
            f"<b>[공모주 분석] {corp_name}</b>\n"
            f"분석 보류 (원문 데이터 파싱 불가)\n\n"
            f'🔗 <a href="{dart_link}">공시 원문 보기</a>'
        )

    verdict = html.escape(score_result["verdict_label"])
    score = score_result["normalized_score"]
    comp_pct = score_result["completeness_pct"]
    cats = score_result["categories"]

    lead_mgr = html.escape(str(data.get("lead_manager") or "미상"))
    sub_date = html.escape(str(data.get("subscription_date") or "미정"))
    band_low = data.get("band_low")
    band_high = data.get("band_high")
    conf_price = data.get("confirmed_price")
    comp_rate = data.get("competition_rate")
    lockup = data.get("lockup_ratio_pct")
    trad_ratio = data.get("tradable_ratio_pct")
    trad_amt = data.get("tradable_amount_eok")

    price_info = f"{conf_price:,}원" if isinstance(conf_price, (int, float)) else "미정"
    if isinstance(band_low, (int, float)) and isinstance(band_high, (int, float)):
        price_info += f" (밴드 {band_low:,}~{band_high:,}원)"

    lines = [
        f"📊 <b>[공모주 분석] {corp_name}</b>",
        f"<b>{verdict}</b>",
        f"<b>🔥 {score}점</b> / 100점",
        f"자료 완성도 {comp_pct}% · 최종 공모가 확정 기준",
        "",
        f"• 주관사: {lead_mgr} | 청약일: {sub_date}",
        f"• 확정공모가: {price_info}",
    ]

    if isinstance(comp_rate, (int, float)):
        lock_str = f"{lockup:.2f}% (신청수량 잠정)" if isinstance(lockup, (int, float)) else "확인 불가"
        lines.append(f"• 기관경쟁률: {comp_rate:,.2f}:1 | 의무확약: {lock_str}")
    if isinstance(trad_ratio, (int, float)):
        amt_str = f" ({trad_amt:,.0f}억)" if isinstance(trad_amt, (int, float)) else ""
        lines.append(f"• 상장일 유통물량: {trad_ratio:.1f}%{amt_str}")

    lines.extend([
        "",
        "<b>[항목별 평가]</b>",
    ])

    for name in ["기업 경쟁력", "밸류에이션", "유통가능 물량", "기관 경쟁률", "가격 제시 분포", "의무보유확약", "공모 구조"]:
        c = cats[name]
        if c["max"] == 0:
            lines.append(f"• {name}: 확인 불가 (0/{c['full_max']})")
        elif c["max"] < c["full_max"]:
            lines.append(f"• {name}: <b>{c['earned']}/{c['full_max']}</b> (확인배점 {c['max']}점)")
        else:
            lines.append(f"• {name}: <b>{c['earned']}/{c['full_max']}</b>")

    pos_list = data.get("positive_factors") or []
    if pos_list:
        lines.append("")
        lines.append("<b>[긍정 요소]</b>")
        for p in pos_list[:3]:
            lines.append(f"- {html.escape(str(p))}")

    risk_list = list(score_result.get("warnings_triggered") or [])
    for r in data.get("risk_factors") or []:
        if r not in risk_list:
            risk_list.append(str(r))

    if risk_list:
        lines.append("")
        lines.append("<b>[위험 요소]</b>")
        for r in risk_list[:4]:
            lines.append(f"- {html.escape(str(r))}")

    final_comment = data.get("final_comment")
    if final_comment:
        lines.append("")
        lines.append("<b>[최종 판단]</b>")
        lines.append(html.escape(str(final_comment)))

    lines.append("")
    lines.append(f'🔗 <a href="{dart_link}">DART 증권신고서 원문 보기</a>')

    return "\n".join(lines)


def build_day1_reminder_message(info: Dict[str, Any]) -> str:
    """일반청약 1일차 당일 발송할 리마인더 메시지"""
    corp_name = html.escape(info.get("corp_name", ""))
    verdict = html.escape(info.get("verdict_label", ""))
    score = info.get("score", 0)
    lead_mgr = html.escape(info.get("lead_manager", "미상"))
    sub_date = html.escape(info.get("subscription_date", ""))
    conf_price = info.get("confirmed_price")
    comp_rate = info.get("competition_rate")
    lockup = info.get("lockup_ratio_pct")
    rcept_no = info.get("rcept_no", "")
    dart_link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

    price_str = f"{conf_price:,}원" if isinstance(conf_price, (int, float)) else "-"
    comp_str = f"{comp_rate:,.2f}:1" if isinstance(comp_rate, (int, float)) else "-"
    lock_str = f"{lockup:.2f}%" if isinstance(lockup, (int, float)) else "-"

    return (
        f"🔔 <b>[오늘 일반청약 1일차] {corp_name}</b>\n"
        f"<b>{verdict} · {score}점/100점</b>\n\n"
        f"• 청약일정: <b>{sub_date}</b>\n"
        f"• 대표주관사: <b>{lead_mgr}</b>\n"
        f"• 확정공모가: {price_str}\n"
        f"• 기관경쟁률: {comp_str} (확약 {lock_str})\n"
        f"• 최종판단: {html.escape(info.get('final_comment', ''))}\n\n"
        f'🔗 <a href="{dart_link}">DART 증권신고서 보기</a>'
    )


def build_withdrawal_message(filing: Dict[str, Any]) -> str:
    """공모주 상장 철회(철회신고서) 감지 시 발송할 즉시 알림 메시지"""
    corp_name = html.escape(filing.get("corp_name", "Unknown").strip())
    rcept_no = filing.get("rcept_no", "").strip()
    rcept_dt = html.escape(filing.get("rcept_dt", "").strip())
    report_nm = html.escape(re.sub(r"\s+", " ", filing.get("report_nm", "")).strip())
    dart_link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

    return (
        f"🚫 <b>[공모주 상장 철회] {corp_name}</b>\n"
        f"• 공시명: {report_nm}\n"
        f"• 접수일: {rcept_dt}\n"
        f"• 안내: 해당 기업의 공모 일정이 철회되어 기존 청약 리마인더를 삭제했습니다.\n\n"
        f'🔗 <a href="{dart_link}">DART 철회신고서 원문 보기</a>'
    )


_BROKER_PATTERNS = [
    (r"케이비증권|KB증권", "KB증권"),
    (r"아이비케이투자증권|IBK투자증권", "IBK투자증권"),
    (r"비엔케이투자증권|BNK투자증권", "BNK투자증권"),
    (r"엔에이치투자증권|NH투자증권", "NH투자증권"),
    (r"미래에셋증권", "미래에셋증권"),
    (r"한국투자증권", "한국투자증권"),
    (r"삼성증권", "삼성증권"),
    (r"신한투자증권", "신한투자증권"),
    (r"대신증권", "대신증권"),
    (r"신영증권", "신영증권"),
    (r"유진투자증권|유진증권", "유진투자증권"),
    (r"하나증권", "하나증권"),
    (r"키움증권", "키움증권"),
    (r"우리투자증권", "우리투자증권"),
    (r"현대차증권", "현대차증권"),
    (r"한화투자증권", "한화투자증권"),
    (r"교보증권", "교보증권"),
    (r"유안타증권", "유안타증권"),
    (r"DB증권|DB금융투자", "DB증권"),
    (r"SK증권", "SK증권"),
    (r"메리츠증권", "메리츠증권"),
    (r"iM증권|하이투자증권", "iM증권"),
    (r"LS증권|이베스트투자증권", "LS증권"),
    (r"다올투자증권", "다올투자증권"),
    (r"상상인증권", "상상인증권"),
    (r"부국증권", "부국증권"),
]

# 청약 초일 전날(D-1)까지 계좌 개설 완료가 필요한 주요 증권사
_D1_PRE_OPEN_BROKERS = {"NH투자증권", "KB증권", "대신증권", "신한투자증권", "신영증권"}


def _extract_brokers_from_overview(overview_text: str) -> List[str]:
    """공모개요의 인수인 섹션에서 청약 가능 증권사를 순서대로 추출"""
    compact = re.sub(r"\s+", " ", overview_text)
    uw_match = re.search(r"(?:인수인|대표주관회사|공동대표주관|공동주관회사)(.{0,900}?)(?:청약기일|납입기일)", compact)
    target = uw_match.group(1) if uw_match else compact[:1500]

    found_positions: List[Tuple[int, str]] = []
    for pattern, canonical in _BROKER_PATTERNS:
        m = re.search(pattern, target, re.IGNORECASE)
        if m:
            found_positions.append((m.start(), canonical))
    found_positions.sort(key=lambda x: x[0])
    return list(dict.fromkeys(name for _, name in found_positions))


def _parse_subscription_period(overview_text: str) -> Optional[Tuple[date, date, str]]:
    """공모개요 텍스트에서 일반청약일(시작일, 종료일, 표기 문자열) 추출"""
    compact = re.sub(r"\s+", " ", overview_text)
    sub_match = re.search(r"(?:일반\s*(?:투자자|청약자)\s*청약일|청약기일)(.{0,300})", compact)
    search_zone = sub_match.group(1) if sub_match else compact[:1800]

    # 패턴 1: 2026년 12월 14일(월) ~ 2026년 12월 15일(화) 또는 ~ 15일
    p_kr = re.search(
        r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일(?:\s*\([월화수목금토일]\))?\s*[~\-]\s*"
        r"(?:(\d{4})\s*년\s*)?(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일",
        search_zone,
    )
    if p_kr:
        sy, sm, sd = int(p_kr.group(1)), int(p_kr.group(2)), int(p_kr.group(3))
        ey = int(p_kr.group(4)) if p_kr.group(4) else sy
        em = int(p_kr.group(5)) if p_kr.group(5) else sm
        ed = int(p_kr.group(6))
    else:
        # 패턴 2: 2026.12.14(월)~12.15(화) 또는 2026.12.14 ~ 2026.12.15
        p_dot = re.search(
            r"(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\s*\([월화수목금토일]\))?\s*[~\-]\s*"
            r"(?:(\d{4})\.)?(?:(\d{1,2})\.)?(\d{1,2})(?:\s*\([월화수목금토일]\))?",
            search_zone,
        )
        if not p_dot:
            return None
        sy, sm, sd = int(p_dot.group(1)), int(p_dot.group(2)), int(p_dot.group(3))
        ey = int(p_dot.group(4)) if p_dot.group(4) else sy
        em = int(p_dot.group(5)) if p_dot.group(5) else sm
        ed = int(p_dot.group(6))

    try:
        start_dt = datetime(sy, sm, sd, tzinfo=KST).date()
        end_dt = datetime(ey, em, ed, tzinfo=KST).date()
        if end_dt < start_dt and ((p_kr and not p_kr.group(4)) or (not p_kr and not p_dot.group(4))):
            end_dt = datetime(sy + 1, em, ed, tzinfo=KST).date()
    except ValueError:
        return None

    label = f"{start_dt.strftime('%m.%d')}~{end_dt.strftime('%m.%d')}"
    return start_dt, end_dt, label


def _extract_calendar_summary_fields(full_text: str) -> Dict[str, Any]:
    """증권신고서/투자설명서 본문에서 청약일정, 주관사, 공모가, 유통비율 추출"""
    overview_matches = list(re.finditer(r"1\.\s*공모개요", full_text))
    if overview_matches:
        pos = overview_matches[-1].start()
        overview_text = full_text[pos : pos + 4500]
    else:
        overview_text = full_text[:4500]

    period = _parse_subscription_period(overview_text)
    if not period:
        return {}

    start_dt, end_dt, date_label = period
    brokers = _extract_brokers_from_overview(overview_text)

    compact_ov = re.sub(r"\s+", " ", overview_text)
    price_str = "미정"
    is_confirmed = False
    m_conf = re.search(r"확정공모가액(?:은|인)?\s*([\d,]+)\s*원", compact_ov)
    m_band = re.search(r"(?:희망공모가액|공모희망가액)(?:은|인|인\s+)?\s*([\d,]+)\s*원?\s*[~\-]\s*([\d,]+)\s*원", compact_ov)
    if m_conf and "협의" not in compact_ov[m_conf.start() : m_conf.end() + 25]:
        price_str = f"{m_conf.group(1)}원(확정)"
        is_confirmed = True
    elif m_band:
        price_str = f"{m_band.group(1)}~{m_band.group(2)}원"
    else:
        m_any_band = re.search(r"([\d,]{4,})\s*원?\s*[~\-]\s*([\d,]{4,})\s*원", compact_ov)
        if m_any_band:
            price_str = f"{m_any_band.group(1)}~{m_any_band.group(2)}원"

    tradable_pct: Optional[float] = None
    compact_full = re.sub(r"\s+", " ", full_text)
    total_tr_matches = re.findall(
        r"(?:의무보유\s*(?:대상|수량|주식)?을\s*제외한[^.]{0,55}?\((?:공모\s*후\s*(?:기준\s*|지분율\s*)?(?:약\s*)?)?(\d{1,2}\.\d{1,2})\s*%\))"
        r"|(?:(\d{1,2}\.\d{1,2})\s*%\s*에\s*해당하는\s*[\d,]+주는\s*상장\s*직후)"
        r"|(?:상장\s*직후\s*(?:매각이\s*가능한\s*)?유통\s*가능\s*(?:물량|주식(?:수|비율)?)은\s*(?:최대\s*)?[\d,]+주\s*\((?:공모\s*후\s*(?:기준\s*|지분율\s*)?(?:약\s*)?|상장예정주식수의\s*)?(\d{1,2}\.\d{1,2})\s*%\))"
        r"|(?:상장\s*직후\s*(?:매각이\s*가능한\s*)?유통\s*가능\s*(?:물량|주식(?:수|비율)?)(?:은|는)\s*(?:상장예정주식수의\s*)?(\d{1,2}\.\d{1,2})\s*%)",
        compact_full,
    )
    tr_vals = [float(next(x for x in tup if x)) for tup in total_tr_matches if any(tup)]
    tr_vals = [v for v in tr_vals if v >= 15.0]
    if tr_vals:
        tradable_pct = tr_vals[-1]
    else:
        fallback_matches = re.findall(
            r"유통\s*가능\s*(?:한\s*물량|물량|주식(?:수|비율)?)(?:은|는|:)[^.\d%]{0,40}?(\d{1,2}\.\d{1,2})\s*%",
            compact_full,
        )
        fb_vals = [float(v) for v in fallback_matches if float(v) >= 15.0]
        if fb_vals:
            tradable_pct = fb_vals[0]

    return {
        "start_date": start_dt,
        "end_date": end_dt,
        "date_label": date_label,
        "brokers": brokers,
        "price_str": price_str,
        "is_confirmed": is_confirmed,
        "tradable_pct": tradable_pct,
    }


def fetch_upcoming_ipo_calendar(exclude_spac: bool = True, lookahead_days: int = 21) -> List[Dict[str, Any]]:
    """향후 N일 이내 청약 예정인 모든 공모주(DART 신고서 기준)의 일정·주관사 수집"""
    today = datetime.now(KST)
    today_date = today.date()
    max_start_date = today_date + timedelta(days=lookahead_days)
    bgn_60 = (today - timedelta(days=60)).strftime("%Y%m%d")
    end_de = today.strftime("%Y%m%d")
    url = "https://opendart.fss.or.kr/api/list.json"

    # 1) 최근 60일 내 비상장법인 지분증권 증권신고서 목록 확보
    corp_map: Dict[str, str] = {}
    page_no = 1
    while page_no <= 6:
        try:
            resp = requests.get(
                url,
                params={
                    "crtfc_key": DART_API_KEY,
                    "bgn_de": bgn_60,
                    "end_de": end_de,
                    "corp_cls": "E",
                    "pblntf_detail_ty": "C001",
                    "page_no": page_no,
                    "page_count": 100,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "000":
                break
            items = data.get("list", [])
            for it in items:
                rnm = it.get("report_nm", "")
                cname = it.get("corp_name", "").strip()
                cc = it.get("corp_code", "").strip()
                if "지분증권" not in rnm or not cc:
                    continue
                if exclude_spac and ("기업인수목적" in cname or "스팩" in cname):
                    continue
                corp_map.setdefault(cc, cname)
            if page_no >= data.get("total_page", 1) or not items:
                break
            page_no += 1
        except Exception as e:
            print(f"⚠️ 주간 캘린더 DART 목록 조회 실패: {e}")
            break

    # 2) 기업별 최신 공시 상태(철회/완료 제외) 및 원문 파싱
    calendar_items: List[Dict[str, Any]] = []
    for cc, cname in corp_map.items():
        try:
            resp = requests.get(
                url,
                params={
                    "crtfc_key": DART_API_KEY,
                    "corp_code": cc,
                    "bgn_de": bgn_60,
                    "end_de": end_de,
                    "pblntf_ty": "C",
                    "page_count": 20,
                },
                timeout=10,
            )
            resp.raise_for_status()
            c_filings = resp.json().get("list", [])
        except Exception:
            continue

        if not c_filings:
            continue

        # 철회신고서 또는 발행실적보고서가 최신 공모 신고서 이후에 제출됐으면 제외
        latest_names = [x.get("report_nm", "") for x in c_filings]
        if any("철회신고서" in nm or "증권발행실적보고서" in nm for nm in latest_names[:3]):
            continue

        # 본문이 담긴 최신 문서 선택 (단순 첨부추가/첨부정정은 후순위)
        candidate_filings = [
            x for x in c_filings
            if ("투자설명서" in x.get("report_nm", "") or "증권신고서(지분증권)" in x.get("report_nm", ""))
        ]
        candidate_filings.sort(
            key=lambda x: (
                0 if ("[첨부추가]" in x.get("report_nm", "") or "[첨부정정]" in x.get("report_nm", "")) else 1,
                x.get("rcept_no", ""),
            ),
            reverse=True,
        )

        parsed_info: Dict[str, Any] = {}
        chosen_rno = ""
        for cf in candidate_filings[:2]:
            rno = cf.get("rcept_no", "").strip()
            txt = _download_dart_xml_text(rno)
            if not txt:
                continue
            parsed = _extract_calendar_summary_fields(txt)
            if parsed:
                parsed_info = parsed
                chosen_rno = rno
                break

        if not parsed_info:
            continue

        if parsed_info["end_date"] < today_date or parsed_info["start_date"] > max_start_date:
            continue

        calendar_items.append({
            "corp_code": cc,
            "corp_name": cname,
            "rcept_no": chosen_rno,
            **parsed_info,
        })

    calendar_items.sort(key=lambda x: (x["start_date"], x["corp_name"]))
    return calendar_items


def build_weekly_calendar_message(items: List[Dict[str, Any]], today_date: date) -> str:
    """주간 공모주 청약 & 계좌 준비 캘린더 메시지 생성"""
    end_window = today_date + timedelta(days=21)
    header_range = f"{today_date.strftime('%m.%d')} ~ {end_window.strftime('%m.%d')}"

    if not items:
        return (
            f"📅 <b>[주간 공모주 청약 캘린더] ({header_range})</b>\n\n"
            f"향후 3주 이내 예정된 일반 공모주 청약 일정이 없습니다."
        )

    lines = [
        f"📅 <b>[주간 공모주 청약 &amp; 계좌 준비 캘린더]</b>",
        f"🗓️ 조회 범위: {header_range} (총 {len(items)}건)",
        "",
    ]

    all_brokers: List[str] = []
    for idx, it in enumerate(items, 1):
        cname = html.escape(it["corp_name"])
        d_label = html.escape(it["date_label"])
        p_str = html.escape(it["price_str"])
        brokers = it.get("brokers") or []
        all_brokers.extend(brokers)
        b_str = html.escape(", ".join(brokers)) if brokers else "확인 필요"
        tr_pct = it.get("tradable_pct")
        tr_str = f" | 유통 {tr_pct:.1f}%" if isinstance(tr_pct, float) else ""
        rno = it.get("rcept_no", "")
        dart_link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}"

        lines.append(f'<b>{idx}. {cname}</b> ({d_label})')
        lines.append(f"• 공모가: {p_str}{tr_str}")
        lines.append(f'• 청약 증권사: <b>{b_str}</b> (<a href="{dart_link}">신고서</a>)')
        lines.append("")

    unique_brokers = list(dict.fromkeys(all_brokers))
    d1_list = [b for b in unique_brokers if b in _D1_PRE_OPEN_BROKERS]
    same_day_list = [b for b in unique_brokers if b not in _D1_PRE_OPEN_BROKERS]

    lines.append("🏦 <b>계좌 사전 준비 가이드</b>")
    if d1_list:
        lines.append(f"• ⚠️ <b>청약 전날(D-1)까지 개설 필수</b>: {html.escape(', '.join(d1_list))}")
    if same_day_list:
        lines.append(f"• ✅ <b>청약 당일 개설 가능</b>: {html.escape(', '.join(same_day_list))}")

    return "\n".join(lines)


def _truncate_telegram_html(message: str, limit: int = 4096) -> str:
    """Telegram 4,096자 제한 초과 시 하단 링크를 보존하며 HTML 태그 손상 없이 안전하게 절삭"""
    if len(message) <= limit:
        return message
    footer = ""
    body = message
    marker = "\n\n🔗 "
    if marker in message:
        body, tail = message.rsplit(marker, 1)
        footer = marker + tail
    suffix = "\n...(생략)" + footer
    max_body = max(200, limit - len(suffix))
    if len(body) > max_body:
        lines = body.splitlines()
        kept: List[str] = []
        total = 0
        for line in lines:
            add_len = len(line) + (1 if kept else 0)
            if total + add_len > max_body:
                break
            kept.append(line)
            total += add_len
        if kept:
            body = "\n".join(kept)
        else:
            body = re.sub(r"<[^>]*$", "", body[:max_body])
    result = body + suffix
    if len(result) > limit:
        result = re.sub(r"<[^>]*$", "", message[: limit - 10]) + "\n...(생략)"
    return result


def send_telegram_message(message: str) -> bool:
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        safe_message = _truncate_telegram_html(message)
        resp = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": safe_message,
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
    parser = argparse.ArgumentParser(description="AI 공모주 100점 스코어링 봇")
    parser.add_argument("--dry-run", action="store_true", help="Telegram 발송 없이 콘솔에 스코어링 결과 출력")
    parser.add_argument("--days", type=int, default=3, help="조회할 최근 일수 (기본값: 3일)")
    parser.add_argument("--weekly-calendar", action="store_true", help="주간 공모주 청약 및 계좌 준비 캘린더 즉시 발송/테스트")
    args = parser.parse_args()

    config = load_config()
    ipo_cfg = config.get("ipo", {})
    if not ipo_cfg.get("enabled", True):
        print("⏭️ 공모주(IPO) 알림이 비활성화되어 있습니다.")
        return

    if not DART_API_KEY or DART_API_KEY.startswith("your-"):
        print("❌ 필수 환경 변수 누락: DART_API_KEY")
        return

    telegram_ready = bool(
        TELEGRAM_TOKEN and not TELEGRAM_TOKEN.startswith("your-")
        and CHAT_ID and not CHAT_ID.startswith("your-")
    )
    if not args.dry_run and not telegram_ready:
        print("❌ TELEGRAM_TOKEN / CHAT_ID 미설정 (테스트 시 --dry-run 옵션을 사용하세요)")
        return

    state_file = Path(ipo_cfg.get("sent_file", "ipo_sent.json"))
    max_history = ipo_cfg.get("max_history", 100)
    only_confirmed = ipo_cfg.get("only_confirmed", True)
    exclude_spac = ipo_cfg.get("exclude_spac", True)

    now_kst = datetime.now(KST)
    today_iso = now_kst.strftime("%Y-%m-%d")

    print(f"🚀 AI 공모주 100점 스코어링 봇 시작 ({now_kst.isoformat()})")
    if args.dry_run:
        print("🧪 [DRY-RUN 모드] Telegram 발송 및 이력 저장을 건너뜁니다.")

    state = load_ipo_state(state_file)
    sent_set = set(state["sent_rcept_nos"])
    reminders = state["scheduled_reminders"]
    state_changed = False

    # 0) 공모주 상장 철회(철회신고서) 감지 및 즉시 알림
    withdrawals = fetch_ipo_withdrawals(days=args.days, exclude_spac=exclude_spac)
    new_withdrawals = [w for w in reversed(withdrawals) if w.get("rcept_no") not in sent_set]
    for w in new_withdrawals:
        w_rno = w.get("rcept_no", "").strip()
        w_cc = w.get("corp_code", "").strip()
        w_cname = w.get("corp_name", "").strip()
        w_msg = build_withdrawal_message(w)
        if args.dry_run:
            print(f"  🚫 [상장 철회 감지] {w_cname}")
            for line in w_msg.splitlines():
                print(f"  {line}")
            print()
        elif send_telegram_message(w_msg):
            print(f"  🚫 상장 철회 알림 발송 완료: {w_cname} ({w_rno})")
            state["sent_rcept_nos"].append(w_rno)
            sent_set.add(w_rno)
            if w_cc in reminders:
                del reminders[w_cc]
            for rk in [k for k, v in reminders.items() if isinstance(v, dict) and v.get("corp_name") == w_cname]:
                del reminders[rk]
            state_changed = True

    # 1) [타이밍 1] 신규 수요예측 확정 공시([발행조건확정]) 스코어링 및 발송
    filings = fetch_ipo_filings(days=args.days, only_confirmed=only_confirmed, exclude_spac=exclude_spac)
    new_filings = [f for f in reversed(filings) if f.get("rcept_no") not in sent_set]

    print(f"✨ 신규 스코어링 대상 공모주: {len(new_filings)}건\n")

    for i, filing in enumerate(new_filings, 1):
        rcept_no = filing.get("rcept_no", "").strip()
        corp_code = filing.get("corp_code", "").strip()
        corp_name = filing.get("corp_name", "").strip()
        report_nm = re.sub(r"\s+", " ", filing.get("report_nm", "")).strip()

        print(f"[{i}/{len(new_filings)}] {corp_name} - {report_nm} ({rcept_no}) 스코어링 분석 중...")
        extracted = extract_ipo_sections(rcept_no, corp_code=corp_code)
        raw_data = analyze_ipo_with_gemini(extracted, corp_name, report_nm) if extracted else None
        score_res = score_ipo_data(raw_data) if raw_data else None

        # 동일 기업의 [발행조건확정] 재정정 시 공모가가 같으면 중복 리포트 발송 방지
        existing_key = corp_code if corp_code in reminders else next(
            (k for k, v in reminders.items() if isinstance(v, dict) and v.get("corp_name") == corp_name),
            None,
        )
        if existing_key and raw_data:
            existing_info = reminders[existing_key]
            new_price = raw_data.get("confirmed_price")
            if new_price is not None and new_price == existing_info.get("confirmed_price"):
                print(f"  ⏭️ 동일 공모가({new_price:,}원) 재정정 공시로 중복 리포트 생략")
                new_sub_start = str(raw_data.get("subscription_start_date") or "").strip()
                if not args.dry_run:
                    if re.match(r"^\d{4}-\d{2}-\d{2}$", new_sub_start) and new_sub_start != existing_info.get("subscription_start_date"):
                        existing_info["subscription_start_date"] = new_sub_start
                        existing_info["subscription_date"] = raw_data.get("subscription_date", existing_info.get("subscription_date", ""))
                        existing_info["rcept_no"] = rcept_no
                        existing_info["reminded"] = False
                    state["sent_rcept_nos"].append(rcept_no)
                    sent_set.add(rcept_no)
                    state_changed = True
                continue

        message = build_ipo_message(filing, raw_data, score_res)

        if args.dry_run:
            print("  📨 [1차 알림: 수요예측 발표 스코어링 리포트]")
            print("  " + "=" * 58)
            for line in message.splitlines():
                print(f"  {line}")
            print("  " + "=" * 58 + "\n")
        elif send_telegram_message(message):
            print(f"  ✅ 1차 스코어링 리포트 발송 완료 ({rcept_no})")
            state["sent_rcept_nos"].append(rcept_no)
            sent_set.add(rcept_no)
            state_changed = True

            # [타이밍 2 예약] 일반청약 1일차 리마인더 등록
            if raw_data and score_res:
                sub_start = str(raw_data.get("subscription_start_date") or "").strip()
                if re.match(r"^\d{4}-\d{2}-\d{2}$", sub_start):
                    reminders[corp_code or rcept_no] = {
                        "corp_name": corp_name,
                        "rcept_no": rcept_no,
                        "subscription_start_date": sub_start,
                        "subscription_date": raw_data.get("subscription_date", ""),
                        "lead_manager": raw_data.get("lead_manager", ""),
                        "confirmed_price": raw_data.get("confirmed_price"),
                        "competition_rate": raw_data.get("competition_rate"),
                        "lockup_ratio_pct": raw_data.get("lockup_ratio_pct"),
                        "score": score_res["normalized_score"],
                        "verdict_label": score_res["verdict_label"],
                        "final_comment": raw_data.get("final_comment", ""),
                        "reminded": False,
                    }
        else:
            print("  ❌ 발송 실패")

    # 2) [타이밍 2] 오늘이 일반청약 1일차인 종목 리마인더 발송
    for key_id, info in list(reminders.items()):
        if not info.get("reminded") and info.get("subscription_start_date") == today_iso:
            msg = build_day1_reminder_message(info)
            if args.dry_run:
                print(f"  🔔 [2차 알림: 오늘 일반청약 1일차 리마인더] {info.get('corp_name')}")
                for line in msg.splitlines():
                    print(f"  {line}")
            elif send_telegram_message(msg):
                print(f"  🔔 청약 1일차 리마인더 발송 완료: {info.get('corp_name')}")
                info["reminded"] = True
                state_changed = True

    # 3) [주간 캘린더] 일요일 오전 또는 --weekly-calendar 지정 시 향후 청약 & 계좌 준비 캘린더 발송
    should_send_weekly = args.weekly_calendar or (
        now_kst.weekday() == 6 and state.get("last_weekly_calendar_date") != today_iso
    )
    if should_send_weekly:
        print("📅 주간 공모주 청약 & 계좌 준비 캘린더 수집 중...")
        cal_items = fetch_upcoming_ipo_calendar(exclude_spac=exclude_spac)
        cal_msg = build_weekly_calendar_message(cal_items, now_kst.date())
        if args.dry_run:
            print("  📅 [주간 공모주 청약 캘린더 미리보기]")
            print("  " + "=" * 58)
            for line in cal_msg.splitlines():
                print(f"  {line}")
            print("  " + "=" * 58 + "\n")
        elif send_telegram_message(cal_msg):
            print(f"  ✅ 주간 공모주 청약 캘린더 발송 완료 ({len(cal_items)}건)")
            state["last_weekly_calendar_date"] = today_iso
            state_changed = True
        else:
            print("  ❌ 주간 공모주 청약 캘린더 발송 실패")

    if state_changed and not args.dry_run:
        save_ipo_state(state_file, state, max_history)
        print("💾 IPO 상태 및 발송 이력 저장 완료")


if __name__ == "__main__":
    main()
