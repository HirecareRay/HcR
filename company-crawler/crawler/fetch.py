"""웹페이지 요청과 텍스트 추출. 페이지 실존 여부를 명확히 구분한다."""
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .config import HEADERS, MAX_TEXT_LENGTH, REQUEST_TIMEOUT

# 인재상/회사소개가 있을 법한 내부 페이지 키워드
SUBPAGE_KEYWORDS = (
    "about", "company", "회사소개", "기업소개", "채용", "recruit", "career",
    "인재", "talent", "culture", "philosophy", "vision", "가치", "value",
    "ceo", "message", "대표", "인사말",
)


@dataclass(frozen=True)
class Page:
    """가져온 페이지 한 개. ok=False면 실존하지 않거나 요청 실패."""

    url: str
    ok: bool
    status_code: int | None
    title: str
    og_site_name: str
    text: str
    error: str | None = None


def _clean_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def fetch_page(url: str) -> Page:
    """단일 URL을 가져온다. 실존 확인(2xx + 본문)을 포함한다."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        return Page(url, False, None, "", "", "", f"요청 실패: {e}")

    if resp.status_code >= 400:
        return Page(url, False, resp.status_code, "", "", "",
                    f"HTTP {resp.status_code}")

    try:
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        return Page(url, False, resp.status_code, "", "", "", f"파싱 실패: {e}")

    title = (soup.title.get_text(strip=True) if soup.title else "")
    og = soup.find("meta", property="og:site_name")
    og_name = (og.get("content", "").strip() if og else "")
    text = _clean_text(soup)

    if not text:
        return Page(resp.url, False, resp.status_code, title, og_name, "",
                    "본문 없음")
    return Page(resp.url, True, resp.status_code, title, og_name,
                text[:MAX_TEXT_LENGTH])


def find_internal_subpages(homepage: Page, limit: int = 3) -> list[str]:
    """홈페이지 본문에서 인재상/회사소개 관련 내부 링크를 찾는다(동일 도메인만)."""
    if not homepage.ok:
        return []
    try:
        resp = requests.get(homepage.url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        return []

    base_host = urlparse(homepage.url).netloc
    found: list[str] = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        label = a.get_text(strip=True).lower()
        if not any(kw in href.lower() or kw in label for kw in SUBPAGE_KEYWORDS):
            continue
        absolute = urljoin(homepage.url, href)
        if urlparse(absolute).netloc != base_host:  # 동일 도메인만 신뢰
            continue
        if absolute not in found and absolute != homepage.url:
            found.append(absolute)
        if len(found) >= limit:
            break
    return found
