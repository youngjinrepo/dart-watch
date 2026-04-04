"""
뉴스 수집 및 알림 모듈
- 다중 소스 지원 (naver, example 등)
- corp_code → 회사명 변환 후 검색
- 요약 기능
"""

from .base import NewsItem, NewsSource
from .corp_resolver import resolve_corp_names
from .summarizer import summarize

__all__ = ['NewsItem', 'NewsSource', 'resolve_corp_names', 'summarize']
