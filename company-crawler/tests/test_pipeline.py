"""파이프라인 폴백 로직 단위 테스트(요청 B: 검증 실패 시 참고 출처 폴백).

네트워크/LLM 호출은 monkeypatch로 대체해 분기 로직만 검증한다.
실행: python -m pytest tests/
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler import pipeline  # noqa: E402
from crawler.fetch import Page  # noqa: E402
from crawler.models import Status  # noqa: E402


def _ok_page(url: str, text: str = "본문", title: str = "title") -> Page:
    return Page(url, True, 200, title, "site", text)


def _patch(monkeypatch, *, homepage_candidates, reference_candidates,
           pages, extract):
    monkeypatch.setattr(pipeline, "find_homepage_candidates",
                        lambda name: homepage_candidates)
    monkeypatch.setattr(pipeline, "find_reference_candidates",
                        lambda name: reference_candidates)
    monkeypatch.setattr(pipeline, "fetch_page", lambda url: pages[url])
    monkeypatch.setattr(pipeline, "find_internal_subpages", lambda page: [])
    monkeypatch.setattr(pipeline, "extract_talent", extract)


def test_verify_failure_falls_back_to_reference(monkeypatch):
    """공식 홈페이지 검증 실패 → 잡사이트 등 참고에서 인재상 수집(REFERENCE_ONLY)."""
    ref_url = "https://www.catch.co.kr/Comp/CompInfo/1"
    _patch(
        monkeypatch,
        homepage_candidates=["https://wrong-company.com"],
        reference_candidates=[ref_url],
        pages={
            "https://wrong-company.com": _ok_page("https://wrong-company.com"),
            ref_url: _ok_page(ref_url, "도전과 신뢰를 추구하는 인재",
                              title="키트넷 기업정보 - 잡코리아"),
        },
        extract=lambda name, text: {
            "talent_values": "도전·신뢰" if "인재" in text else None,
            "talent_context": None, "business_description": None},
    )
    # 홈페이지 후보는 검증 실패하도록 강제한다.
    monkeypatch.setattr(pipeline, "verify_homepage",
                        lambda name, page: (False, "운영사 불일치"))

    result = pipeline.process_company("키트넷")

    assert result.status == Status.REFERENCE_ONLY
    assert result.reference_talent_values == "도전·신뢰"
    assert result.reference_urls[0] == ref_url
    # 검증 실패 사유는 보존돼 출처 신뢰도를 설명한다.
    assert result.verify_reason == "운영사 불일치"


def test_verify_failure_without_reference_keeps_failure_status(monkeypatch):
    """참고에서도 인재상을 못 찾으면 원래 실패 상태(verify_failed)를 유지한다."""
    ref_url = "https://blog.example.com/x"
    _patch(
        monkeypatch,
        homepage_candidates=["https://wrong-company.com"],
        reference_candidates=[ref_url],
        pages={
            "https://wrong-company.com": _ok_page("https://wrong-company.com"),
            ref_url: _ok_page(ref_url, "관련 없는 글",
                              title="키트넷 기업정보 - 잡코리아"),
        },
        extract=lambda name, text: {
            "talent_values": None, "talent_context": None,
            "business_description": None},
    )
    monkeypatch.setattr(pipeline, "verify_homepage",
                        lambda name, page: (False, "운영사 불일치"))

    result = pipeline.process_company("키트넷")

    assert result.status == Status.VERIFY_FAILED
    assert result.reference_talent_values is None
    assert result.reference_urls == (ref_url,)


def test_mismatched_company_reference_is_skipped(monkeypatch):
    """제목이 다른 회사인 참고 페이지는 인재상이 있어도 출처로 신뢰하지 않는다."""
    wrong = "https://www.saramin.co.kr/wrong"
    right = "https://www.jobkorea.co.kr/company/right"
    _patch(
        monkeypatch,
        homepage_candidates=["https://wrong-company.com"],
        reference_candidates=[wrong, right],
        pages={
            "https://wrong-company.com": _ok_page("https://wrong-company.com"),
            # 검색이 엉뚱하게 반환한 '러쉬코리아' 공고(대상은 '러쉬에잇')
            wrong: _ok_page(wrong, "유연한 사고를 가진 인재",
                            title="러쉬코리아 디자이너 채용 - 사람인"),
            right: _ok_page(right, "도전하는 인재",
                            title="러쉬에잇 기업정보 - 잡코리아"),
        },
        extract=lambda name, text: {
            "talent_values": "도전" if "도전" in text else "유연한 사고",
            "talent_context": None, "business_description": None},
    )
    monkeypatch.setattr(pipeline, "verify_homepage",
                        lambda name, page: (False, "운영사 불일치"))

    result = pipeline.process_company("러쉬에잇")

    # 오매칭(러쉬코리아) 페이지는 건너뛰고, 일치하는 페이지만 출처로 채택한다.
    assert result.status == Status.REFERENCE_ONLY
    assert result.reference_talent_values == "도전"
    assert result.reference_urls == (right,)
    assert wrong not in result.reference_urls


def test_homepage_success_skips_reference(monkeypatch):
    """공식 홈페이지에서 인재상을 찾으면 참고 폴백을 타지 않는다(SUCCESS)."""
    hp = "https://real.com"
    _patch(
        monkeypatch,
        homepage_candidates=[hp],
        reference_candidates=["https://should-not-be-used.com"],
        pages={hp: _ok_page(hp, "창의와 열정의 인재상")},
        extract=lambda name, text: {
            "talent_values": "창의·열정", "talent_context": None,
            "business_description": "소프트웨어"},
    )
    monkeypatch.setattr(pipeline, "verify_homepage",
                        lambda name, page: (True, "사이트명 일치"))

    result = pipeline.process_company("리얼")

    assert result.status == Status.SUCCESS
    assert result.talent_values == "창의·열정"
    assert result.talent_values_source == hp
    assert result.reference_urls == ()
