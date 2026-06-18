"""공식 홈페이지 후보 URL 탐색. 여기서는 신뢰하지 않고 '후보'만 모은다.

실제 소유 검증은 verify 모듈이 담당한다.
"""
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .config import (
    BLOCKED_DOMAINS,
    HEADERS,
    MAX_URL_CANDIDATES,
    REFERENCE_JOBSITES,
    REQUEST_TIMEOUT,
)


def _is_blocked(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return any(blocked in host or blocked in url.lower() for blocked in BLOCKED_DOMAINS)


def _root(url: str) -> str | None:
    """URL을 스킴+호스트 루트로 정규화한다."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return None


def _dedup(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _homepage_queries(company_name: str) -> list[str]:
    """공식 홈페이지를 찾기 위한 검색어 변형 목록.

    기업명만으로는 잘 안 나오는 경우가 많아 '주식회사/(주)/채용' 변형을 함께 쓴다.
    """
    name = company_name.strip()
    return [
        f"{name} 공식 홈페이지",
        f"{name} 주식회사",
        f"{name}(주)",
        f"{name} 채용 인재상",
    ]


def find_homepage_candidates(company_name: str) -> list[str]:
    """기업명에 대한 공식 홈페이지 후보 루트 URL 목록을 반환한다."""
    queries = _homepage_queries(company_name)
    candidates = _google_candidates(queries) + _naver_candidates(queries)
    roots = [r for r in (_root(u) for u in candidates) if r and not _is_blocked(r)]
    return _dedup(roots)[:MAX_URL_CANDIDATES]


def _google_candidates(queries: list[str]) -> list[str]:
    try:
        from googlesearch import search

        out: list[str] = []
        for query in queries:
            for url in search(query, num_results=4, lang="ko"):
                if not _is_blocked(url):
                    out.append(url)
        return out
    except Exception:
        return []


def _naver_candidates(queries: list[str]) -> list[str]:
    """네이버 검색 결과의 외부 링크를 후보로 수집(차단 도메인 제외)."""
    out: list[str] = []
    for query in queries[:2]:  # 네이버는 상위 2개 변형만(요청 수 절약)
        try:
            quoted = requests.utils.quote(query)
            resp = requests.get(
                f"https://search.naver.com/search.naver?query={quoted}",
                headers=HEADERS, timeout=REQUEST_TIMEOUT,
            )
            soup = BeautifulSoup(resp.text, "lxml")
            out.extend(
                a["href"] for a in soup.select("a[href]")
                if a.get("href", "").startswith("http") and not _is_blocked(a["href"])
            )
        except Exception:
            continue
    return out


def _reference_queries(company_name: str) -> list[str]:
    """참고 출처 검색어. 일반 검색 + 잡사이트 site: 타겟 검색을 함께 만든다.

    잡사이트는 기업 인재상을 정형 페이지로 노출해 폴백 신뢰도가 높다.
    """
    name = company_name.strip()
    queries = [f"{name} 인재상"]
    queries += [f"{name} 인재상 site:{site}" for site in REFERENCE_JOBSITES]
    return queries


def _jobsite_rank(url: str) -> int:
    """잡사이트 우선순위 인덱스. 잡사이트가 아니면 맨 뒤로 보낸다."""
    host = (urlparse(url).netloc or "").lower()
    for i, site in enumerate(REFERENCE_JOBSITES):
        if site in host:
            return i
    return len(REFERENCE_JOBSITES)


def rank_references(urls: list[str]) -> list[str]:
    """크롤 안정성이 높은 잡사이트를 앞으로 정렬(안정 정렬로 원순서 유지)."""
    return sorted(urls, key=_jobsite_rank)


# 참고 검색에서 버릴 검색엔진/소셜 내부 링크(잡사이트·뉴스·블로그는 유지).
_REFERENCE_JUNK_HOSTS = (
    "naver.com", "google.", "daum.net", "youtube.com",
    "facebook.com", "instagram.com",
)


def _is_reference_junk(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return any(junk in host for junk in _REFERENCE_JUNK_HOSTS)


def _google_reference_candidates(company_name: str) -> list[str]:
    out: list[str] = []
    try:
        from googlesearch import search

        for query in _reference_queries(company_name):
            for url in search(query, num_results=4, lang="ko"):
                if _root(url):
                    out.append(url)
    except Exception:
        pass
    return out


def _naver_reference_candidates(company_name: str) -> list[str]:
    """네이버 검색의 외부 링크를 참고 후보로 수집(잡사이트 유지).

    구글 스크래퍼가 차단/레이트리밋으로 0건을 주는 상황의 안정적 백업이다.
    """
    out: list[str] = []
    try:
        quoted = requests.utils.quote(f"{company_name} 인재상")
        resp = requests.get(
            f"https://search.naver.com/search.naver?query={quoted}",
            headers=HEADERS, timeout=REQUEST_TIMEOUT,
        )
        soup = BeautifulSoup(resp.text, "lxml")
        out.extend(
            a["href"] for a in soup.select("a[href]")
            if a.get("href", "").startswith("http")
            and not _is_reference_junk(a["href"])
        )
    except Exception:
        pass
    return out


def find_reference_candidates(company_name: str) -> list[str]:
    """홈페이지 외 인재상 참고용 URL 후보(잡사이트 우선).

    구글 + 네이버 두 백엔드를 합쳐 한쪽이 막혀도 후보를 확보한다.
    """
    out = _google_reference_candidates(company_name)
    out += _naver_reference_candidates(company_name)
    valid = [u for u in out if _root(u)]
    return rank_references(_dedup(valid))
