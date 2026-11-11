"""
DART 공시 자동 알림 시스템
- DART Open API를 사용한 실시간 공시 조회
- Telegram 메시지 발송
- rcept_no 기반 중복 방지 (해시 미사용)
"""

import os
import io
import re
import sys
import html
import json
import zipfile
import argparse
import warnings
import requests
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from pathlib import Path
from dotenv import load_dotenv
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# 한국 표준시 (KST, UTC+9)
KST = timezone(timedelta(hours=9))


# .env 파일 로드
load_dotenv()

# 환경 변수 (민감한 정보는 .env에서만 로드)
DART_API_KEY = os.getenv('DART_API_KEY')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# 설정 파일 로드
CONFIG_FILE = Path('config.json')


def load_config() -> Dict:
    """
    config.json에서 설정 정보 로드
    
    Returns:
        Dict: 설정 정보
    """
    if not CONFIG_FILE.exists():
        print(f"❌ 설정 파일을 찾을 수 없습니다: {CONFIG_FILE}")
        raise FileNotFoundError(f"설정 파일이 필요합니다: {CONFIG_FILE}")
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            print(f"✅ 설정 파일 로드: {CONFIG_FILE}")
            return config
    except json.JSONDecodeError as e:
        print(f"❌ 설정 파일 파싱 실패: {e}")
        raise


# 설정 로드
CONFIG = load_config()

# 설정값 언팩
SENT_FILE = Path(CONFIG.get('sent_file', 'sent.json'))
MAX_HISTORY = CONFIG.get('max_history', 100)
DART_BASE_URL = CONFIG.get('dart_api_url', 'https://opendart.fss.or.kr/api/list.json')
API_PAGE_COUNT = min(CONFIG.get('api_page_count', 100), 100)
CORP_CODES = CONFIG.get('corp_codes', [])
KEYWORDS = CONFIG.get('keywords', [])
SUMMARY_CFG = CONFIG.get('summary', {})


def load_sent_history() -> List[str]:
    """
    이미 발송한 rcept_no 목록 로드 (발송 순서 유지)
    
    Returns:
        List[str]: 발송 완료한 rcept_no 리스트
    """
    if not SENT_FILE.exists():
        return []
    
    try:
        with open(SENT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                return [str(item) for item in data]
            return []
    except Exception as e:
        print(f"⚠️ 발송 이력 로드 실패: {e}")
        return []


def save_sent_history(rcept_nos: List[str]) -> None:
    """
    발송한 rcept_no 목록 저장 (순서 유지, 중복 제거 후 최근 N개만 유지)
    
    Args:
        rcept_nos: 발송한 rcept_no 리스트
    """
    try:
        unique_ordered = list(dict.fromkeys(rcept_nos))
        recent = unique_ordered[-MAX_HISTORY:]
        with open(SENT_FILE, 'w', encoding='utf-8') as f:
            json.dump(recent, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ 발송 이력 저장 실패: {e}")


def _fetch_paginated_list(bgn_de: str, end_de: str, corp_code: Optional[str] = None) -> List[Dict]:
    """DART list.json 페이지네이션 조회 헬퍼"""
    results: List[Dict] = []
    page_no = 1
    max_pages = 30  # 최대 3,000건

    while page_no <= max_pages:
        params = {
            'crtfc_key': DART_API_KEY,
            'bgn_de': bgn_de,
            'end_de': end_de,
            'page_no': page_no,
            'page_count': API_PAGE_COUNT,
        }
        if corp_code:
            params['corp_code'] = corp_code

        response = requests.get(DART_BASE_URL, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        status = data.get('status')

        # 013: 조회된 데이터가 없음
        if status == '013':
            break

        if status != '000':
            print(f"⚠️ DART API 에러 (page {page_no}): {data.get('message', 'Unknown error')}")
            break

        page_list = data.get('list', [])
        results.extend(page_list)

        total_page = data.get('total_page', 1)
        if page_no >= total_page or not page_list:
            break

        page_no += 1

    return results


def fetch_dart_announcements(days: Optional[int] = None) -> List[Dict]:
    """
    DART API에서 공시 목록 조회 (KST 기준)
    - days 지정 시: 최근 N일간 조회 (테스트용)
    - 평일: 당일 공시만
    - 주말(토/일): 금요일부터 당일까지
    - 감시 대상 기업이 소수(1~5개)면 기업 코드 지정 조회, 아니면 전체 조회
    
    Returns:
        List[Dict]: 공시 정보 리스트
    """
    try:
        today = datetime.now(KST)
        
        if days is not None and days > 0:
            start_date = today - timedelta(days=days)
            end_date = today
        elif today.weekday() >= 5:  # 5=토요일, 6=일요일
            days_to_friday = today.weekday() - 4  # 토요일:1, 일요일:2
            start_date = today - timedelta(days=days_to_friday)
            end_date = today
        else:
            start_date = today
            end_date = today
        
        bgn_de = start_date.strftime('%Y%m%d')
        end_de = end_date.strftime('%Y%m%d')
        
        print(f"📅 조회 기간 (KST): {bgn_de} ~ {end_de}")
        
        announcements: List[Dict] = []
        if 1 <= len(CORP_CODES) <= 5:
            for code in CORP_CODES:
                announcements.extend(_fetch_paginated_list(bgn_de, end_de, corp_code=code))
        else:
            announcements = _fetch_paginated_list(bgn_de, end_de)
        
        print(f"📊 조회된 공시 총 {len(announcements)}개")
        
        return announcements
    
    except requests.RequestException as e:
        print(f"❌ DART API 호출 실패: {e}")
        return []
    except json.JSONDecodeError as e:
        print(f"❌ DART API 응답 파싱 실패: {e}")
        return []


def filter_announcements(
    announcements: List[Dict],
    corp_codes: List[str],
    keywords: List[str]
) -> List[Dict]:
    """
    공시 목록을 기업 코드와 키워드로 필터링
    
    Args:
        announcements: 원본 공시 목록
        corp_codes: 감시 대상 기업 코드
        keywords: 포함할 키워드
    
    Returns:
        List[Dict]: 필터링된 공시 목록
    """
    filtered = []
    
    # 기업 코드 필터링 (빈 배열이면 전체 기업 허용)
    if corp_codes:
        code_match = [ann for ann in announcements if ann.get('corp_code', '') in corp_codes]
    else:
        code_match = list(announcements)
    print(f"📌 기업 코드 필터링: {len(announcements)} → {len(code_match)}개")
    if code_match:
        print(f"   샘플: {code_match[0].get('corp_name', 'N/A')} - {code_match[0].get('report_nm', 'N/A')}")
    
    # 유효 키워드 추출 (공백/빈 문자열 제외)
    valid_keywords = [k.strip() for k in (keywords or []) if k and k.strip()]
    
    for ann in code_match:
        title = ann.get('report_nm', '')
        
        # 키워드 필터링 (유효 키워드가 있을 때만 필터링, 하나라도 포함되면 통과)
        if valid_keywords and not any(keyword.lower() in title.lower() for keyword in valid_keywords):
            continue
        
        filtered.append(ann)
    
    print(f"🔍 키워드 필터링: {len(code_match)} → {len(filtered)}개 공시 해당")
    return filtered


def _extract_clean_text(raw_bytes: bytes) -> str:
    """HTML/XML 바이트에서 불필요한 태그 제거 후 텍스트 추출"""
    soup = BeautifulSoup(raw_bytes, 'html.parser')
    for tag in soup(['script', 'style']):
        tag.decompose()
    return soup.get_text(separator='\n', strip=True)


def fetch_announcement_text(rcept_no: str) -> Optional[str]:
    """
    DART 공시 원문 텍스트 조회
    1차: DART Open API (document.xml ZIP 다운로드)
    2차 (fallback): DART 웹 뷰어 (dsaf001/main.do -> report/viewer.do)
    """
    # 1차: DART Open API 공시서류원문파일 조회
    if DART_API_KEY:
        try:
            doc_url = "https://opendart.fss.or.kr/api/document.xml"
            resp = requests.get(
                doc_url,
                params={'crtfc_key': DART_API_KEY, 'rcept_no': rcept_no},
                timeout=15,
            )
            resp.raise_for_status()
            if resp.content[:2] == b'PK':  # ZIP 시그니처 확인
                with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                    for name in z.namelist():
                        raw_bytes = z.read(name)
                        text = _extract_clean_text(raw_bytes)
                        if text:
                            return text[:4000]
        except Exception as e:
            print(f"⚠️ OpenAPI 원문 조회 실패 (웹 뷰어로 재시도): {e}")

    # 2차: DART 웹 뷰어 스크래핑 (fallback)
    main_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        main_resp = requests.get(main_url, timeout=10, headers=headers)
        main_resp.raise_for_status()
        match = re.search(
            r'viewDoc\(["\'](\d+)["\']\s*,\s*["\'](\d+)["\']\s*,\s*["\']([^"\']*)["\']\s*,\s*["\']([^"\']*)["\']\s*,\s*["\']([^"\']*)["\']\s*,\s*["\']([^"\']*)["\']',
            main_resp.text,
        )
        if not match:
            return None

        params = {
            'rcpNo': match.group(1),
            'dcmNo': match.group(2),
            'eleId': match.group(3),
            'offset': match.group(4),
            'length': match.group(5),
            'dtd': match.group(6),
        }
        viewer_resp = requests.get(
            "https://dart.fss.or.kr/report/viewer.do",
            params=params,
            timeout=10,
            headers=headers,
        )
        viewer_resp.raise_for_status()
        text = _extract_clean_text(viewer_resp.content)
        return text[:4000] if text else None
    except Exception as e:
        print(f"⚠️ 공시 원문 조회 실패: {e}")
        return None


def summarize_simple(text: str, max_len: int = 350) -> Optional[str]:
    """
    AI API 키가 없을 때 공시 원문에서 핵심 내용을 발췌
    - 상단 문서 헤더(회사명/공시명 등) 및 빈 값('-') 라인을 정리하고 항목별로 한 줄씩 묶어 가독성 개선
    """
    if not text:
        return None

    lines = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line and line != '-']

    # 첫 줄이 '회사명/보고서명/(날짜)...' 형태면 제거
    if lines and '/' in lines[0] and '(' in lines[0]:
        lines = lines[1:]

    # 두 번째 줄이 단순 보고서 제목 반복이면 제거
    if len(lines) >= 2 and len(lines[0]) < 40 and not re.match(r'^\d+\.', lines[0]):
        lines = lines[1:]

    # 번호('1. ', '2. ')나 기호('-', '※')로 시작하는 줄을 기준으로 항목 병합
    merged: List[str] = []
    for line in lines:
        if re.match(r'^(\d+\.|[-※•])', line):
            merged.append(line)
        elif merged:
            # 이전 줄이 번호 항목이고 아직 콜론(':')이 없으면 ': '로 연결, 아니면 공백으로 연결
            if re.match(r'^\d+\.', merged[-1]) and ':' not in merged[-1]:
                merged[-1] = f"{merged[-1]}: {line}"
            else:
                merged[-1] = f"{merged[-1]} {line}"
        else:
            merged.append(line)

    # 값이 비어 있는 번호 항목(예: '4. 시가배당률(%): 보통주식 종류주식') 정리 후 결합
    result = '\n'.join(merged).strip()
    if not result:
        return None
    if len(result) > max_len:
        return result[:max_len].rstrip() + '…'
    return result


def summarize_announcement(text: str, corp_name: str, title: str) -> Optional[str]:
    """설정에 따라 공시 요약 생성 (API 키 미설정/실패 시 simple 원문 발췌로 자동 대체)"""
    if not text or not SUMMARY_CFG.get('enabled', True):
        return None

    method = SUMMARY_CFG.get('method', 'gemini')
    if method == 'none':
        return None

    if method == 'gemini':
        gemini_summary = summarize_with_gemini(text, corp_name, title)
        if gemini_summary:
            return gemini_summary
    elif method == 'claude':
        claude_summary = summarize_with_claude(text, corp_name, title)
        if claude_summary:
            return claude_summary

    return summarize_simple(text)


def summarize_with_gemini(text: str, corp_name: str, title: str) -> Optional[str]:
    """Google Gemini API (gemini-2.5-flash)로 공시 요약"""
    api_key = os.getenv('GEMINI_API_KEY', '').strip()
    if not api_key or api_key.startswith('your-'):
        return None
    try:
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        prompt = (
            f"{corp_name}의 '{title}' 공시입니다.\n"
            f"투자자 관점에서 핵심 수치와 내용을 3줄 이내의 개조식(•)으로 요약해주세요.\n"
            f"마크다운 기호(**, #, * 등)는 사용하지 말고 순수 텍스트로만 작성해주세요.\n\n{text}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 1024,
                "temperature": 0.2,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        response = requests.post(
            url,
            headers={"x-goog-api-key": api_key},
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            texts = [p.get("text", "") for p in parts if not p.get("thought") and "text" in p]
            result = "\n".join(texts).strip()
            if result:
                return result
        return None
    except Exception as e:
        print(f"⚠️ Gemini 요약 실패: {e}")
        return None


def summarize_with_claude(text: str, corp_name: str, title: str) -> Optional[str]:
    """Claude Haiku로 공시 요약"""
    api_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
    if not api_key or api_key.startswith('your-'):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    f"{corp_name}의 '{title}' 공시입니다.\n"
                    f"투자자 관점에서 핵심만 3줄 이내로 요약해주세요.\n\n{text}"
                )
            }]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"⚠️ Claude 요약 실패: {e}")
        return None


def build_message(announcement: Dict, summary: Optional[str] = None) -> str:
    """
    공시 정보를 Telegram 메시지로 변환

    Args:
        announcement: 공시 정보 딕셔너리
        summary: Claude 요약 텍스트 (없으면 생략)

    Returns:
        str: Telegram 메시지
    """
    raw_corp = announcement.get('corp_name', 'Unknown').strip()
    raw_title = re.sub(r'\s+', ' ', announcement.get('report_nm', 'No title')).strip()
    raw_date = announcement.get('rcept_dt', 'N/A').strip()

    corp_name = html.escape(raw_corp)
    title = html.escape(raw_title)
    rcept_date = html.escape(raw_date)
    rcept_no = announcement.get('rcept_no', '').strip()

    dart_link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

    message = (
        f"📰 <b>{corp_name}</b>\n"
        f"📋 {title}\n"
        f"📅 {rcept_date}\n"
    )

    if summary:
        message += f"\n💡 {html.escape(summary)}\n"

    message += f'\n🔗 <a href="{dart_link}">공시 보기</a>'

    return message


def send_telegram_message(message: str) -> bool:
    """
    Telegram으로 메시지 발송
    
    Args:
        message: 발송할 메시지
    
    Returns:
        bool: 발송 성공 여부
    """
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        
        payload = {
            'chat_id': CHAT_ID,
            'text': message,
            'parse_mode': 'HTML',  # HTML 형식 지원
        }
        
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        
        result = response.json()
        
        if result.get('ok'):
            return True
        else:
            print(f"⚠️ Telegram 발송 실패: {result.get('description', 'Unknown error')}")
            return False
    
    except requests.RequestException as e:
        print(f"❌ Telegram API 호출 실패: {e}")
        return False


def main():
    """메인 실행 함수"""
    parser = argparse.ArgumentParser(description="DART 공시 알림 시스템")
    parser.add_argument("--dry-run", action="store_true", help="Telegram 발송 및 이력 저장 없이 콘솔에 메시지 출력")
    parser.add_argument("--days", type=int, default=None, help="최근 N일간 공시 조회 (테스트용)")
    args = parser.parse_args()

    # 환경 변수 확인
    telegram_ready = bool(
        TELEGRAM_TOKEN and not TELEGRAM_TOKEN.startswith('your-')
        and CHAT_ID and not CHAT_ID.startswith('your-')
    )
    if not DART_API_KEY or DART_API_KEY.startswith('your-'):
        print("❌ 필수 환경 변수 누락: DART_API_KEY")
        return

    if not args.dry_run and not telegram_ready:
        print("❌ 필수 환경 변수 누락 (또는 테스트 실행 시 --dry-run 옵션을 사용하세요):")
        print(f"  - TELEGRAM_TOKEN 설정됨: {bool(TELEGRAM_TOKEN and not TELEGRAM_TOKEN.startswith('your-'))}")
        print(f"  - CHAT_ID 설정됨: {bool(CHAT_ID and not CHAT_ID.startswith('your-'))}")
        return
    
    print(f"🚀 DART 공시 알림 시스템 시작 ({datetime.now(KST).isoformat()})")
    if args.dry_run:
        print("🧪 [DRY-RUN 모드] Telegram 발송 및 sent.json 저장을 건너뜁니다.")
    print(f"📍 감시 대상: {', '.join(CORP_CODES) if CORP_CODES else '전체'}")
    print(f"🔑 필터 키워드: {', '.join(k for k in KEYWORDS if k) or '없음 (전체 공시)'}\n")
    
    # 1단계: 발송 이력 로드
    sent_history = load_sent_history()
    sent_set = set(sent_history)
    print(f"📂 이전 발송 이력: {len(sent_history)}개")
    
    # 2단계: DART API에서 공시 조회
    announcements = fetch_dart_announcements(days=args.days)
    
    if not announcements:
        print("⏭️ 조회된 공시가 없습니다")
        return
    
    # 3단계: 공시 필터링
    filtered = filter_announcements(announcements, CORP_CODES, KEYWORDS)
    
    if not filtered:
        print("⏭️ 필터링 조건에 맞는 공시가 없습니다")
        return
    
    # 4단계: 중복 제거 (오래된 공시부터 시간순 발송을 위해 역순 정렬)
    new_announcements = [
        ann for ann in reversed(filtered)
        if ann.get('rcept_no') not in sent_set
    ]
    
    print(f"✨ 신규 공시: {len(new_announcements)}개\n")
    
    if not new_announcements:
        print("✅ 이미 전송된 공시입니다")
        return
    
    # 5단계: 공시별 메시지 전송
    successfully_sent = []
    
    for i, announcement in enumerate(new_announcements, 1):
        rcept_no = announcement.get('rcept_no', '').strip()
        corp_name = announcement.get('corp_name', 'Unknown').strip()
        title = re.sub(r'\s+', ' ', announcement.get('report_nm', '')).strip()
        
        # 요약 생성 (설정 활성화 시)
        summary = None
        if SUMMARY_CFG.get('enabled') and SUMMARY_CFG.get('method') != 'none':
            print(f"  🤖 요약 추출 중...")
            text = fetch_announcement_text(rcept_no)
            if text:
                summary = summarize_announcement(text, corp_name, title)

        # 메시지 생성 및 전송
        message = build_message(announcement, summary)

        print(f"[{i}/{len(new_announcements)}] {corp_name} - {title}")
        
        if args.dry_run:
            print("  📨 [DRY-RUN 메시지 미리보기]")
            for line in message.splitlines():
                print(f"     {line}")
            print()
        elif send_telegram_message(message):
            print(f"  ✅ 발송 완료 (rcept_no: {rcept_no})")
            successfully_sent.append(rcept_no)
        else:
            print(f"  ❌ 발송 실패")
    
    # 6단계: 발송 이력 업데이트
    if successfully_sent and not args.dry_run:
        # 기존 이력(순서 보장) + 새로운 발송 이력
        updated_history = sent_history + successfully_sent
        save_sent_history(updated_history)
        print(f"\n💾 발송 이력 저장 완료 ({min(len(updated_history), MAX_HISTORY)}개)")
    
    print(f"\n✨ 작업 완료 ({datetime.now(KST).isoformat()})")


if __name__ == '__main__':
    main()
