"""
회사명으로 DART corp_code 조회
- corpCode.xml (전체 목록 ZIP) 다운로드 후 검색
사용법: python find_corp_code.py
"""
import os
import io
import zipfile
import xml.etree.ElementTree as ET
import requests
from dotenv import load_dotenv

load_dotenv()
DART_API_KEY = os.getenv('DART_API_KEY')

COMPANIES = ['메리츠금융지주', 'BYC', '한미약품', '한미반도체']

print("📥 DART 전체 기업 코드 목록 다운로드 중...")
resp = requests.get(
    'https://opendart.fss.or.kr/api/corpCode.xml',
    params={'crtfc_key': DART_API_KEY},
    timeout=30,
)
resp.raise_for_status()

with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
    xml_data = z.read('CORPCODE.xml')

root = ET.fromstring(xml_data)
all_corps = [
    {
        'corp_code': item.findtext('corp_code', ''),
        'corp_name': item.findtext('corp_name', ''),
        'stock_code': item.findtext('stock_code', ''),
    }
    for item in root.findall('list')
]

print(f"✅ 총 {len(all_corps)}개 기업 로드 완료\n")

for name in COMPANIES:
    matches = [c for c in all_corps if name in c['corp_name']]
    print(f"[{name}] 검색 결과 {len(matches)}개:")
    for m in matches:
        print(f"  corp_code={m['corp_code']}  corp_name={m['corp_name']}  stock_code={m['stock_code']}")
    print()
