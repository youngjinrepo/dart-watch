"""
DART 공시 자동 알림 시스템
- DART Open API를 사용한 실시간 공시 조회
- Telegram 메시지 발송
- rcept_no 기반 중복 방지 (해시 미사용)
"""

import os
import json
import requests
from datetime import datetime
from typing import List, Dict, Set
from pathlib import Path
from dotenv import load_dotenv


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
API_PAGE_COUNT = CONFIG.get('api_page_count', 50)
CORP_CODES = CONFIG.get('corp_codes', [])
KEYWORDS = CONFIG.get('keywords', [])


def load_sent_history() -> Set[str]:
    """
    이미 발송한 rcept_no 목록 로드
    
    Returns:
        Set[str]: 발송 완료한 rcept_no 세트
    """
    if not SENT_FILE.exists():
        return set()
    
    try:
        with open(SENT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 리스트 또는 딕셔너리 형태 모두 지원
            if isinstance(data, list):
                return set(data)
            return set()
    except Exception as e:
        print(f"⚠️ 발송 이력 로드 실패: {e}")
        return set()


def save_sent_history(rcept_nos: List[str]) -> None:
    """
    발송한 rcept_no 목록 저장 (최근 N개만 유지)
    
    Args:
        rcept_nos: 발송한 rcept_no 리스트
    """
    try:
        # 최근 MAX_HISTORY개만 유지
        recent = rcept_nos[-MAX_HISTORY:]
        with open(SENT_FILE, 'w', encoding='utf-8') as f:
            json.dump(recent, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ 발송 이력 저장 실패: {e}")


def fetch_dart_announcements() -> List[Dict]:
    """
    DART API에서 공시 목록 조회
    - 평일: 당일 공시만
    - 주말(토/일): 금요일부터 당일까지
    
    Returns:
        List[Dict]: 공시 정보 리스트
    """
    try:
        today = datetime.now()
        
        # 주말인 경우 금요일부터, 평일인 경우 당일
        if today.weekday() >= 5:  # 5=토요일, 6=일요일
            # 금요일로 설정 (오늘부터 금요일까지의 차이 계산)
            days_to_friday = today.weekday() - 4  # 토요일:1, 일요일:2
            start_date = today - __import__('datetime').timedelta(days=days_to_friday)
            end_date = today
        else:
            # 평일: 당일만
            start_date = today
            end_date = today
        
        bgn_de = start_date.strftime('%Y%m%d')
        end_de = end_date.strftime('%Y%m%d')
        
        print(f"📅 조회 기간: {bgn_de} ~ {end_de}")
        
        params = {
            'crtfc_key': DART_API_KEY,
            'bgn_de': bgn_de,  # 시작 일자
            'end_de': end_de,  # 종료 일자
            'page_count': API_PAGE_COUNT,  # 1회 조회 최대 50개
        }
        
        response = requests.get(DART_BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        # API 성공 여부 확인
        if data.get('status') != '000':
            print(f"⚠️ DART API 에러: {data.get('message', 'Unknown error')}")
            return []
        
        announcements = data.get('list', [])
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
    
    # 기업 코드 필터링
    code_match = [ann for ann in announcements if ann.get('corp_code', '') in corp_codes]
    print(f"📌 기업 코드 필터링: {len(announcements)} → {len(code_match)}개")
    if code_match:
        print(f"   샘플: {code_match[0].get('corp_name', 'N/A')} - {code_match[0].get('report_nm', 'N/A')}")
    
    for ann in code_match:
        corp_code = ann.get('corp_code', '')
        title = ann.get('report_nm', '')
        
        # 키워드 필터링 (하나라도 포함되면 통과)
        if not any(keyword.lower() in title.lower() for keyword in keywords):
            continue
        
        filtered.append(ann)
    
    print(f"🔍 키워드 필터링: {len(code_match)} → {len(filtered)}개 공시 해당")
    return filtered


def build_message(announcement: Dict) -> str:
    """
    공시 정보를 Telegram 메시지로 변환
    
    Args:
        announcement: 공시 정보 딕셔너리
    
    Returns:
        str: Telegram 메시지
    """
    corp_name = announcement.get('corp_name', 'Unknown')
    title = announcement.get('report_nm', 'No title')
    rcept_date = announcement.get('rcept_dt', 'N/A')
    rcept_no = announcement.get('rcept_no', '')
    corp_code = announcement.get('corp_code', '')
    
    # DART 공시 링크 구성
    # 작동하는 형식: https://dart.fss.or.kr/cgi-bin/browse.cgi?action=html&rcept_no=XXXXXXXXX
    dart_link = f"https://dart.fss.or.kr/cgi-bin/browse.cgi?action=html&rcept_no={rcept_no}"
    
    message = (
        f"📰 <b>{corp_name}</b>\n"
        f"📋 {title}\n"
        f"📅 {rcept_date}\n"
        f"🔗 <a href=\"{dart_link}\">공시 보기</a>"
    )
    
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
    
    # 환경 변수 확인
    if not all([DART_API_KEY, TELEGRAM_TOKEN, CHAT_ID]):
        print("❌ 필수 환경 변수 누락:")
        print(f"  - DART_API_KEY: {bool(DART_API_KEY)}")
        print(f"  - TELEGRAM_TOKEN: {bool(TELEGRAM_TOKEN)}")
        print(f"  - CHAT_ID: {bool(CHAT_ID)}")
        return
    
    print(f"🚀 DART 공시 알림 시스템 시작 ({datetime.now().isoformat()})")
    print(f"📍 감시 대상: {', '.join(CORP_CODES)}")
    print(f"🔑 필터 키워드: {', '.join(KEYWORDS)}\n")
    
    # 1단계: 발송 이력 로드
    sent_history = load_sent_history()
    print(f"📂 이전 발송 이력: {len(sent_history)}개")
    
    # 2단계: DART API에서 공시 조회
    announcements = fetch_dart_announcements()
    
    if not announcements:
        print("⏭️ 조회된 공시가 없습니다")
        return
    
    # 3단계: 공시 필터링
    filtered = filter_announcements(announcements, CORP_CODES, KEYWORDS)
    
    if not filtered:
        print("⏭️ 필터링 조건에 맞는 공시가 없습니다")
        return
    
    # 4단계: 중복 제거 및 전송
    new_announcements = [
        ann for ann in filtered
        if ann.get('rcept_no') not in sent_history
    ]
    
    print(f"✨ 신규 공시: {len(new_announcements)}개\n")
    
    if not new_announcements:
        print("✅ 이미 전송된 공시입니다")
        return
    
    # 5단계: 공시별 메시지 전송
    successfully_sent = []
    
    for i, announcement in enumerate(new_announcements, 1):
        rcept_no = announcement.get('rcept_no', '')
        corp_name = announcement.get('corp_name', 'Unknown')
        title = announcement.get('report_nm', '')
        
        # 메시지 생성 및 전송
        message = build_message(announcement)
        
        print(f"[{i}/{len(new_announcements)}] {corp_name} - {title}")
        
        if send_telegram_message(message):
            print(f"  ✅ 발송 완료 (rcept_no: {rcept_no})")
            successfully_sent.append(rcept_no)
        else:
            print(f"  ❌ 발송 실패")
    
    # 6단계: 발송 이력 업데이트
    if successfully_sent:
        # 기존 이력 + 새로운 발송 이력
        updated_history = list(sent_history) + successfully_sent
        save_sent_history(updated_history)
        print(f"\n💾 발송 이력 저장 완료 ({len(updated_history)}개)")
    
    print(f"\n✨ 작업 완료 ({datetime.now().isoformat()})")


if __name__ == '__main__':
    main()
