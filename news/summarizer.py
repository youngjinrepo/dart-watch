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


def summarize(item: NewsItem, method: str = 'gemini') -> str:
    """
    뉴스 요약

    Args:
        item: 뉴스 아이템
        method: gemini | openai | simple

    Returns:
        요약된 텍스트
    """
    if method == 'gemini':
        return _summarize_gemini(item) or _summarize_simple(item)
    if method == 'openai':
        return _summarize_openai(item) or _summarize_simple(item)
    return _summarize_simple(item)


def _summarize_simple(item: NewsItem) -> str:
    """앞부분 추출 (HTML 제거된 description 또는 title 사용)"""
    text = item.description.strip() or item.title.strip()
    if len(text) <= SIMPLE_SUMMARY_LEN:
        return text
    return text[:SIMPLE_SUMMARY_LEN].rstrip() + '…'


def _summarize_gemini(item: NewsItem) -> Optional[str]:
    """Gemini API로 요약 (GEMINI_API_KEY 필요)"""
    api_key = os.getenv('GEMINI_API_KEY', '').strip()
    if not api_key or api_key.startswith('your-'):
        return None

    try:
        text = (item.description or item.title or '').strip()
        if not text or len(text) < 50:
            return _summarize_simple(item)

        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        payload = {
            "contents": [{
                "parts": [{
                    "text": f"다음 뉴스 내용을 마크다운 기호 없이 한국어로 2~3문장으로 핵심만 요약해주세요.\n\n{text[:3000]}"
                }]
            }],
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
    except Exception:
        return None


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
