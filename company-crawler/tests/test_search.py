"""참고 출처(폴백) 검색어 생성·정렬 단위 테스트.

네트워크 없이 순수 함수만 검증한다. 실행: python -m pytest tests/
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.config import REFERENCE_JOBSITES  # noqa: E402
from crawler.search import (  # noqa: E402
    _is_reference_junk,
    _reference_queries,
    rank_references,
)


def test_reference_queries_include_generic_and_jobsite_targets():
    queries = _reference_queries("카카오")
    assert "카카오 인재상" in queries
    # 각 잡사이트에 대한 site: 타겟 검색어가 포함돼야 한다.
    for site in REFERENCE_JOBSITES:
        assert f"카카오 인재상 site:{site}" in queries


def test_reference_queries_strip_whitespace():
    assert _reference_queries("  네이버 ")[0] == "네이버 인재상"


def test_rank_references_puts_jobsites_first():
    urls = [
        "https://blog.example.com/post",
        "https://www.jobkorea.co.kr/company/123",
        "https://www.catch.co.kr/Comp/CompInfo",
    ]
    ranked = rank_references(urls)
    # catch가 jobkorea보다 우선순위가 높고, 둘 다 일반 블로그보다 앞선다.
    assert ranked[0].startswith("https://www.catch.co.kr")
    assert ranked[1].startswith("https://www.jobkorea.co.kr")
    assert ranked[-1].startswith("https://blog.example.com")


def test_rank_references_is_stable_for_non_jobsites():
    urls = ["https://a.com/1", "https://b.com/2"]
    assert rank_references(urls) == urls


def test_reference_junk_filters_search_engine_and_social():
    # 검색엔진/소셜 내부 링크는 참고 후보에서 버린다.
    assert _is_reference_junk("https://search.naver.com/search.naver?q=x")
    assert _is_reference_junk("https://www.google.com/search?q=x")
    assert _is_reference_junk("https://www.youtube.com/watch?v=x")


def test_reference_junk_keeps_jobsites_and_blogs():
    # 잡사이트·블로그·뉴스는 유지해야 한다.
    assert not _is_reference_junk("https://www.jobkorea.co.kr/company/1")
    assert not _is_reference_junk("https://www.catch.co.kr/Comp/CompInfo/1")
    assert not _is_reference_junk("https://someblog.tistory.com/1")
