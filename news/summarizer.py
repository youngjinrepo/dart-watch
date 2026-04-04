"""
뉴스 요약
- simple: 앞부분 N자 추출
- openai: OpenAI API 요약 (선택)
"""

import os
import requests
from typing import Optional
from .base import NewsItem

# 요약 최대 길이 (simple 방식)
SIMPLE_SUMMARY_LEN = 150


def summarize(item: NewsItem, method: str = 'simple') -> str:
    """
    뉴스 요약

    Args:
        item: 뉴스 아이템
        method: simple | openai

    Returns:
        요약된 텍스트
    """
    if method == 'openai':
        return _summarize_openai(item) or _summarize_simple(item)
    return _summarize_simple(item)


def _summarize_simple(item: NewsItem) -> str:
    """앞부분 추출 (HTML 제거된 description 또는 title 사용)"""
    text = item.description.strip() or item.title.strip()
    if len(text) <= SIMPLE_SUMMARY_LEN:
        return text
    return text[:SIMPLE_SUMMARY_LEN].rstrip() + '…'


def _summarize_openai(item: NewsItem) -> Optional[str]:
    """OpenAI API로 요약 (OPENAI_API_KEY 필요)"""
    api_key = os.getenv('OPENAI_API_KEY', '')
    if not api_key:
        return None

    try:
        text = (item.description or item.title or '').strip()
        if not text or len(text) < 50:
            return _summarize_simple(item)

        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': 'gpt-4o-mini',
                'messages': [
                    {'role': 'system', 'content': '뉴스 내용을 2~3문장으로 요약해주세요. 한국어로 답변하세요.'},
                    {'role': 'user', 'content': text[:3000]},
                ],
                'max_tokens': 200,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
        return content.strip() if content else None
    except Exception:
        return None
