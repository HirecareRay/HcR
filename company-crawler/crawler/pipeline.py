"""기업 1건 처리 파이프라인. 검색→검증→수집→추출을 조립한다."""
from .config import MAX_REFERENCE_URLS
from .extract import extract_talent
from .fetch import Page, fetch_page, find_internal_subpages
from .models import Result, Status
from .search import find_homepage_candidates, find_reference_candidates
from .verify import mentions_company, verify_homepage


def process_company(company_name: str) -> Result:
    """한 기업의 인재상을 수집한다. 상태로 None/오류를 구분해 반환한다."""
    base = Result(company_name=company_name, status=Status.URL_NOT_FOUND)

    homepage, reason = _find_verified_homepage(company_name)
    if homepage is None:
        # 공식 홈페이지 검증 실패 시에도 잡사이트 등 외부 참고로 폴백한다(요청 B).
        # 참고를 못 찾으면 원래 실패 상태(verify_failed 등)를 그대로 유지한다.
        failed = base.with_values(status=reason[0], verify_reason=reason[1])
        return _collect_from_references(company_name, failed, empty_status=reason[0])

    text, source = _collect_homepage_text(homepage)
    try:
        extracted = extract_talent(company_name, text)
    except Exception as e:
        return base.with_values(
            status=Status.EXTRACT_ERROR, official_url=homepage.url,
            url_verified=True, error=f"인재상 추출 실패: {e}")

    found = base.with_values(
        official_url=homepage.url, url_verified=True,
        verify_reason="공식 홈페이지 검증됨",
        business_description=extracted["business_description"])

    if extracted["talent_values"]:
        return found.with_values(
            status=Status.SUCCESS, talent_values=extracted["talent_values"],
            talent_values_source=source)

    return _collect_from_references(company_name, found, empty_status=Status.NO_DATA)


def _find_verified_homepage(company_name: str):
    """후보 URL을 검증해 첫 공식 홈페이지 Page를 찾는다.

    실패 시 (None, (Status, 사유))를 반환한다.
    """
    candidates = find_homepage_candidates(company_name)
    if not candidates:
        return None, (Status.URL_NOT_FOUND, "공식 홈페이지 후보를 찾지 못함")

    last_reason = "후보 모두 검증 실패"
    any_ok = False
    for url in candidates:
        page = fetch_page(url)
        if page.ok:
            any_ok = True
        verified, reason = verify_homepage(company_name, page)
        if verified:
            return page, (Status.SUCCESS, reason)
        last_reason = reason

    status = Status.VERIFY_FAILED if any_ok else Status.CRAWL_ERROR
    return None, (status, last_reason)


def _collect_homepage_text(homepage: Page) -> tuple[str, str]:
    """홈페이지 + 동일 도메인 내부 소개/채용 페이지 텍스트를 합친다."""
    parts = [homepage.text]
    for sub_url in find_internal_subpages(homepage):
        sub = fetch_page(sub_url)
        if sub.ok:
            parts.append(sub.text)
    return " ".join(parts), homepage.url


def _collect_from_references(
    company_name: str, result: Result, empty_status: Status
) -> Result:
    """홈페이지에 인재상이 없을 때 외부 참고(잡사이트 우선)에서 분리 수집한다.

    인재상을 찾으면 REFERENCE_ONLY로, 못 찾으면 empty_status를 유지한다
    (홈페이지가 정상이었으면 NO_DATA, 검증 실패였으면 그 실패 상태).
    """
    references: list[str] = []
    found_value: str | None = None
    found_url: str | None = None

    for url in find_reference_candidates(company_name)[:MAX_REFERENCE_URLS]:
        page = fetch_page(url)
        if not page.ok:
            continue
        # 기업명 매칭 가드: 제목/사이트명이 대상 기업과 일치하는 페이지만 신뢰한다.
        # 다른 회사 공고는 출처로도 기록하지 않아 오매칭 인재상을 차단한다.
        if not mentions_company(company_name, page):
            continue
        references.append(page.url)
        if found_value is None:
            try:
                extracted = extract_talent(company_name, page.text)
            except Exception:
                continue
            if extracted["talent_values"]:
                found_value = extracted["talent_values"]
                found_url = page.url

    if found_value:
        # 출처 URL 기록: 인재상을 실제로 추출한 페이지를 맨 앞에 둔다.
        ordered = (found_url,) + tuple(u for u in references if u != found_url)
        return result.with_values(
            status=Status.REFERENCE_ONLY,
            reference_talent_values=found_value,
            reference_urls=ordered)

    return result.with_values(
        status=empty_status, reference_urls=tuple(references))
