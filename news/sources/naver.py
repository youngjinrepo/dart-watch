"""
네이버 뉴스 검색 API 소스
- https://developers.naver.com/docs/serviceapi/search/news/news.md
- 환경 변수: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
"""

import os
import re
import requests
from typing import List
from ..base import NewsItem, NewsSource


class NaverNewsSource(NewsSource):
    """네이버 뉴스 검색 API"""

    def __init__(self, config: dict):
        self.config = config or {}
        self.client_id = os.getenv('NAVER_CLIENT_ID', '')
        self.client_secret = os.getenv('NAVER_CLIENT_SECRET', '')
        self.display = self.config.get('display_per_query', 10)

    @property
    def source_id(self) -> str:
        return 'naver'

    def is_available(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def fetch(self, query: str, limit: int = 10) -> List[NewsItem]:
        if not self.is_available():
            return []

        try:
            url = 'https://openapi.naver.com/v1/search/news.json'
            headers = {
                'X-Naver-Client-Id': self.client_id,
                'X-Naver-Client-Secret': self.client_secret,
            }
            params = {
                'query': query,
                'display': min(limit, 100),
                'sort': 'date',
            }

            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            items = []
            for item in data.get('items', []):
                title = self._clean_html(item.get('title', ''))
                description = self._clean_html(item.get('description', ''))
                items.append(NewsItem(
                    title=title,
                    link=item.get('link', ''),
                    description=description,
                    pub_date=item.get('pubDate', ''),
                    source='naver',
                    query=query,
                ))
            return items

        except requests.RequestException:
            return []
        except (KeyError, TypeError):
            return []

    @staticmethod
    def _clean_html(text: str) -> str:
        """HTML 태그 제거"""
        if not text:
            return ''
        text = re.sub(r'<[^>]+>', '', text)
        return text.replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
