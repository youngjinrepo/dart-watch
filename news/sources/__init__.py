"""
뉴스 소스 구현
- naver: 네이버 뉴스 검색 API
- example: 예제/플레이스홀더 (실제 API 없이 구조만)
"""

from ..base import NewsSource
from .naver import NaverNewsSource
from .example import ExampleNewsSource

# config.json의 sources에 등록할 수 있는 소스 매핑
SOURCES = {
    'naver': NaverNewsSource,
    'example': ExampleNewsSource,
}


def get_source(source_id: str, config: dict):
    """소스 ID로 인스턴스 생성"""
    cls = SOURCES.get(source_id)
    if not cls:
        raise ValueError(f"알 수 없는 뉴스 소스: {source_id}. 사용 가능: {list(SOURCES.keys())}")
    return cls(config)
