"""검증 핵심 로직(이름 정규화/매칭) 단위 테스트.

네트워크/LLM 없이 순수 함수만 검증한다. 실행: python -m pytest tests/
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.fetch import Page  # noqa: E402
from crawler.verify import mentions_company, name_matches, normalize_name  # noqa: E402


def _page(title: str, og: str = "") -> Page:
    return Page("https://x", True, 200, title, og, "본문")


def test_normalize_strips_corp_suffix_and_symbols():
    assert normalize_name("(주)카카오") == "카카오"
    assert normalize_name("삼성전자 주식회사") == "삼성전자"
    assert normalize_name("Naver Corp.") == "naver"


def test_name_matches_in_title():
    assert name_matches("카카오", "카카오 | 회사소개", "")
    assert name_matches("Naver", "NAVER Corporation", "")


def test_name_matches_via_og_site_name():
    assert name_matches("쿠팡", "", "쿠팡 Coupang")


def test_name_does_not_match_other_company():
    # 다른 회사 페이지가 잘못 잡히는 상황을 막아야 한다(핵심 버그).
    assert not name_matches("카카오", "네이버 회사소개", "NAVER")
    assert not name_matches("삼성전자", "LG전자 채용", "LG Electronics")


def test_too_short_name_is_not_trusted():
    # 1글자 정규화 결과는 오탐 위험으로 매칭하지 않는다.
    assert not name_matches("A", "ABC Company", "")


def test_empty_signals_do_not_match():
    assert not name_matches("카카오", "", "")
    assert not name_matches("카카오", None, "")


def test_short_latin_abbreviation_requires_exact_match():
    # 'FF'가 'FF패널'(다른 회사 제품)에 우연히 포함돼도 매칭되면 안 된다(핵심 버그).
    assert not name_matches("FF", "FF패널 자재 전문 시공", "한길에코")
    assert not name_matches("FF", "Final Fantasy 공식", "")
    # 사이트가 실제로 'FF'를 운영 주체로 선언하면(정확 일치) 인정한다.
    assert name_matches("FF", "FF", "FF")


def test_short_latin_abbreviation_not_matched_as_substring():
    # 짧은 영문 약어는 부분 포함으로 통과시키지 않는다.
    assert not name_matches("kt", "ktotelecom 회사소개", "")


def test_mentions_company_accepts_matching_reference_page():
    # 제목에 대상 기업명이 있으면 참고 출처로 신뢰한다.
    assert mentions_company("한세엠케이", _page("2026년 한세엠케이 채용 | 인크루트"))


def test_mentions_company_rejects_other_company_posting():
    # 검색이 반환한 다른 회사 공고는 거부한다(핵심: 오매칭 차단).
    assert not mentions_company("러쉬에잇", _page("러쉬코리아 디자이너 채용 - 사람인"))
    assert not mentions_company("인포벨리코리아", _page("씨엠티정보통신 기업정보 - 잡코리아"))


def test_mentions_company_rejects_failed_page():
    assert not mentions_company("카카오", Page("https://x", False, 404, "", "", ""))
