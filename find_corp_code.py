"""
회사명으로 DART corp_code 조회
사용법: python find_corp_code.py
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()
DART_API_KEY = os.getenv('DART_API_KEY')

COMPANIES = ['메리츠금융지주', 'BYC', '한미약품', '한미반도체']

for name in COMPANIES:
    params = {
        'crtfc_key': DART_API_KEY,
        'corp_name': name,
        'page_count': 5,
    }
    resp = requests.get('https://opendart.fss.or.kr/api/company.json', params=params, timeout=10)
    data = resp.json()

    if data.get('status') != '000':
        print(f"[{name}] 조회 실패: {data.get('message')}")
        continue

    results = data.get('list', [])
    print(f"\n[{name}] 검색 결과 {len(results)}개:")
    for r in results:
        print(f"  corp_code={r.get('corp_code')}  corp_name={r.get('corp_name')}  stock_code={r.get('stock_code')}")
