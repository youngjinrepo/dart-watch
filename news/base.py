"""
뉴스 소스 추상 인터페이스
- 새 소스 추가 시 이 클래스를 상속하여 구현
- news/sources/ 에 소스 모듈 추가 후 config.json sources에 등록
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class NewsItem:
    """뉴스 아이템 공통 구조"""
    title: str
    link: str
    description: str
    pub_date: str
    source: str  # 소스 식별자 (naver, example 등)
    query: str   # 검색에 사용된 쿼리 (회사명 또는 키워드)


class NewsSource(ABC):
    """뉴스 소스 추상 클래스"""

    @property
    @abstractmethod
    def source_id(self) -> str:
        """소스 식별자 (config의 source_config 키와 일치)"""
        pass

    @abstractmethod
    def fetch(self, query: str, limit: int = 10) -> List[NewsItem]:
        """
        검색어로 뉴스 조회

        Args:
            query: 검색어 (회사명 또는 키워드)
            limit: 가져올 최대 개수

        Returns:
            NewsItem 리스트
        """
        pass

    def is_available(self) -> bool:
        """API 키 등 필수 설정이 있는지 확인"""
        return True
