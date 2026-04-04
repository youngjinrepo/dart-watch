"""
DART API 응답 필드 구조 확인
"""
import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DART_API_KEY = os.getenv('DART_API_KEY')
DART_BASE_URL = 'https://opendart.fss.or.kr/api/list.json'

today = datetime.now().strftime('%Y%m%d')

params = {
    'crtfc_key': DART_API_KEY,
    'bgn_de': today,
    'end_de': today,
    'page_count': 5,
}

response = requests.get(DART_BASE_URL, params=params, timeout=10)
data = response.json()

announcements = data.get('list', [])

if announcements:
    print("📋 첫 번째 공시 데이터 구조:\n")
    first = announcements[0]
    
    print(json.dumps(first, indent=2, ensure_ascii=False))
    
    print("\n\n🔗 링크 관련 필드:")
    for key in first.keys():
        if 'url' in key.lower() or 'link' in key.lower() or 'href' in key.lower() or 'docno' in key.lower() or 'rcept' in key.lower():
            print(f"  {key}: {first[key]}")
