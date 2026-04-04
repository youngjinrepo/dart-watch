"""
뉴스 수집 및 Telegram 알림
- corp_codes → 회사명 변환 후 검색
- 다중 소스 지원 (config에서 sources 지정)
- 요약 후 발송
"""

import os
import json
from datetime import datetime
from pathlib import Path
from typing import List, Set
from dotenv import load_dotenv

import requests

load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
CONFIG_FILE = Path('config.json')


def load_config():
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_sent_history(sent_file: Path) -> Set[str]:
    """발송한 뉴스 링크 목록"""
    if not sent_file.exists():
        return set()
    try:
        with open(sent_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def save_sent_history(sent_file: Path, links: List[str], max_history: int):
    recent = links[-max_history:]
    with open(sent_file, 'w', encoding='utf-8') as f:
        json.dump(recent, f, ensure_ascii=False, indent=2)


def send_telegram(message: str) -> bool:
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        response = requests.post(url, json={
            'chat_id': CHAT_ID,
            'text': message,
            'parse_mode': 'HTML',
        }, timeout=10)
        response.raise_for_status()
        return response.json().get('ok', False)
    except Exception:
        return False


def build_news_message(item, summary: str) -> str:
    return (
        f"<b>[{item.query}]</b>\n"
        f"{item.title}\n"
        f"{summary}\n"
        f'<a href="{item.link}">기사 보기</a>'
    )


def main():
    if not all([TELEGRAM_TOKEN, CHAT_ID]):
        print("[ERROR] TELEGRAM_TOKEN, CHAT_ID 환경 변수가 필요합니다.")
        return

    config = load_config()
    news_cfg = config.get('news', {})
    if not news_cfg.get('enabled'):
        print("[SKIP] 뉴스 알림이 비활성화되어 있습니다. config.json news.enabled 를 true로 설정하세요.")
        return

    corp_codes = config.get('corp_codes', [])
    corp_names_cfg = config.get('corp_names', {})
    keywords = config.get('keywords', []) or ['']
    sources = news_cfg.get('sources', ['naver'])
    source_config = news_cfg.get('source_config', {})
    sent_file = Path(news_cfg.get('sent_file', 'news_sent.json'))
    max_history = news_cfg.get('max_history', 200)
    summary_cfg = news_cfg.get('summary', {})
    summary_enabled = summary_cfg.get('enabled', True)
    summary_method = summary_cfg.get('method', 'simple')

    # corp_code → 회사명
    from news.corp_resolver import resolve_corp_names
    corp_names = resolve_corp_names(corp_codes, corp_names_cfg)
    search_queries = [n for n in corp_names.values() if n]
    valid_keywords = [k for k in (keywords or []) if k and k.strip()]
    search_queries.extend(valid_keywords)

    if not search_queries:
        print("[SKIP] 검색할 회사/키워드가 없습니다.")
        return

    sent_history = load_sent_history(sent_file)
    print(f"[INFO] 이전 발송 이력: {len(sent_history)}개")
    print(f"[INFO] 검색어: {', '.join(search_queries)}\n")

    from news.sources import get_source
    from news.summarizer import summarize
    from news.base import NewsItem

    all_items: List[NewsItem] = []
    seen_links: Set[str] = set()

    for source_id in sources:
        src_cfg = source_config.get(source_id, {})
        if not src_cfg.get('enabled', True):
            continue
        try:
            source = get_source(source_id, src_cfg)
            if not source.is_available():
                print(f"[WARN] {source_id}: API 키 등 설정이 없어 건너뜁니다.")
                continue
            display = src_cfg.get('display_per_query', 5)
            for query in search_queries:
                items = source.fetch(query, limit=display)
                for item in items:
                    if item.link and item.link not in seen_links:
                        seen_links.add(item.link)
                        all_items.append(item)
        except ValueError as e:
            print(f"[WARN] {source_id}: {e}")

    new_items = [i for i in all_items if i.link not in sent_history]
    print(f"[INFO] 신규 뉴스: {len(new_items)}개\n")

    if not new_items:
        print("[DONE] 발송할 신규 뉴스가 없습니다.")
        return

    successfully_sent = []
    for i, item in enumerate(new_items, 1):
        summary = summarize(item, summary_method) if summary_enabled else item.description or item.title
        message = build_news_message(item, summary)
        print(f"[{i}/{len(new_items)}] {item.query} - {item.title[:40]}...")
        if send_telegram(message):
            successfully_sent.append(item.link)
            print("  [OK] 발송 완료")
        else:
            print("  [FAIL] 발송 실패")

    if successfully_sent:
        updated = list(sent_history) + successfully_sent
        save_sent_history(sent_file, updated, max_history)
        print(f"\n[SAVE] 발송 이력 저장 완료 ({len(updated)}개)")

    print(f"\n[DONE] 뉴스 알림 완료 ({datetime.now().isoformat()})")


if __name__ == '__main__':
    main()
