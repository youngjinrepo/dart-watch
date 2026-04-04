"""
예제/플레이스홀더 뉴스 소스
- 실제 API 없이 구조만 제공
- 새 소스 구현 시 참고용
"""

from typing import List
from ..base import NewsItem, NewsSource


class ExampleNewsSource(NewsSource):
    """예제 소스 (실제 데이터 없음)"""

    def __init__(self, config: dict):
        self.config = config or {}

    @property
    def source_id(self) -> str:
        return 'example'

    def is_available(self) -> bool:
        return True

    def fetch(self, query: str, limit: int = 10) -> List[NewsItem]:
        # 실제 구현 시 여기서 API 호출
        # return [NewsItem(...) for ...]
        return []
