"""
corp_code → 회사명 변환
- config의 corp_names에 매핑이 있으면 사용
- 없으면 DART 기업개황 API로 조회
"""

import os
import requests
from typing import Dict, List
from pathlib import Path

# DART 기업개황 API
DART_COMPANY_URL = 'https://opendart.fss.or.kr/api/company.json'


def resolve_corp_names(
    corp_codes: List[str],
    config_corp_names: Dict[str, str],
    dart_api_key: str = None,
) -> Dict[str, str]:
    """
    corp_code → 회사명 매핑 생성

    Args:
        corp_codes: 기업 코드 목록
        config_corp_names: config.json의 corp_names (수동 매핑)
        dart_api_key: DART API 키 (config에 없을 때 API 조회용)

    Returns:
        {corp_code: corp_name} 딕셔너리
    """
    result = {}
    api_key = dart_api_key or os.getenv('DART_API_KEY', '')

    for code in corp_codes:
        # 1) config에 있으면 사용
        if config_corp_names and code in config_corp_names:
            result[code] = config_corp_names[code]
            continue

        # 2) DART API로 조회
        if api_key:
            name = _fetch_corp_name_from_dart(code, api_key)
            if name:
                result[code] = name
                continue

        # 3) fallback: 코드 그대로
        result[code] = code

    return result


def _fetch_corp_name_from_dart(corp_code: str, api_key: str):
    """DART 기업개황 API로 회사명 조회"""
    try:
        params = {
            'crtfc_key': api_key,
            'corp_code': corp_code,
        }
        response = requests.get(DART_COMPANY_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get('status') == '000':
            return data.get('corp_name', '').strip() or None
        return None
    except Exception:
        return None
