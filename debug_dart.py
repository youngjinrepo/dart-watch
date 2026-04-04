"""
DART API 응답 데이터 확인용 스크립트
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
    'page_count': 50,
}

response = requests.get(DART_BASE_URL, params=params, timeout=10)
data = response.json()

print(f"🔍 오늘({today}) DART API 응답 분석\n")
print(f"상태: {data.get('status')}")
print(f"메시지: {data.get('message')}\n")

announcements = data.get('list', [])
print(f"총 공시: {len(announcements)}개\n")

# 기업별 그룹
corp_groups = {}
for ann in announcements:
    corp_code = ann.get('corp_code', 'Unknown')
    corp_name = ann.get('corp_name', 'Unknown')
    report_nm = ann.get('report_nm', 'Unknown')
    
    if corp_code not in corp_groups:
        corp_groups[corp_code] = {'name': corp_name, 'count': 0, 'reports': []}
    
    corp_groups[corp_code]['count'] += 1
    corp_groups[corp_code]['reports'].append(report_nm)

print("📊 기업별 공시 현황 TOP 10:\n")
for corp_code, info in sorted(corp_groups.items(), key=lambda x: x[1]['count'], reverse=True)[:10]:
    print(f"  {corp_code} ({info['name']}): {info['count']}개")
    for report in info['reports'][:2]:
        print(f"    - {report}")
    if len(info['reports']) > 2:
        print(f"    ... 외 {len(info['reports']) - 2}개")
    print()

# 삼성전자 확인
samsung = corp_groups.get('005930', {})
if samsung:
    print(f"✅ 삼성전자(005930) 발견: {samsung['count']}개")
else:
    print(f"❌ 삼성전자(005930) 없음 (오늘 공시 없을 수 있음)")
