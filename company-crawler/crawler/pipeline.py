"""기업 1건 처리 파이프라인. 검색→검증→수집→추출을 조립한다."""
from .config import MAX_REFERENCE_URLS
from .extract import extract_talent
from .fetch import Page, fetch_page, find_internal_subpages
from .models import Result, Status
from .search import find_homepage_candidates, find_reference_candidates
from .verify import verify_homepage


def process_company(company_name: str) -> Result:
    """한 기업의 인재상을 수집한다. 상태로 None/오류를 구분해 반환한다."""
    base = Result(company_name=company_name, status=Status.URL_NOT_FOUND)

    homepage, reason = _find_verified_homepage(company_name)
    if homepage is None:
        return base.with_values(status=reason[0], verify_reason=reason[1])

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

    return _collect_from_references(company_name, found)


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


def _collect_from_references(company_name: str, result: Result) -> Result:
    """홈페이지에 인재상이 없을 때 외부 참고에서 분리 수집한다(요청 5번)."""
    references: list[str] = []
    reference_value: str | None = None

    for url in find_reference_candidates(company_name)[:MAX_REFERENCE_URLS]:
        page = fetch_page(url)
        if not page.ok:
            continue
        references.append(page.url)
        if reference_value is None:
            try:
                extracted = extract_talent(company_name, page.text)
                reference_value = extracted["talent_values"]
            except Exception:
                continue

    if reference_value:
        return result.with_values(
            status=Status.REFERENCE_ONLY,
            reference_talent_values=reference_value,
            reference_urls=tuple(references))

    return result.with_values(
        status=Status.NO_DATA, reference_urls=tuple(references))
