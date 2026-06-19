import csv
import json
import os
import time
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise EnvironmentError("OPENAI_API_KEY가 .env 파일에 설정되지 않았습니다.")

client = OpenAI(api_key=OPENAI_API_KEY)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

MAX_TEXT_LENGTH = 4500
REQUEST_TIMEOUT = 10
DELAY_BETWEEN_COMPANIES = 2

# 공식 홈페이지가 아닌데 검색 상위에 자주 잡히는 도메인(채용·기업정보·SNS·백과·공공 포털).
# search_company_url 과 _fallback_naver_search 양쪽에서 공통으로 사용한다.
BLOCKED_DOMAINS = (
    # 검색/SNS/백과
    "google.com", "search.naver.com", "blog.naver.com", "cafe.naver.com",
    "post.naver.com", "in.naver.com", "linkedin.com", "facebook.com",
    "instagram.com", "youtube.com", "twitter.com", "x.com", "tistory.com",
    "namu.wiki", "wikipedia.org",
    # 채용 포털
    "jobkorea.co.kr", "saramin.co.kr", "wanted.co.kr", "rocketpunch.com",
    "jobplanet.co.kr", "incruit.com", "gamejob.co.kr", "albamon.com",
    "catch.co.kr", "linkareer.com", "worknet.go.kr", "jasoseol.com",
    # 기업정보·신용·공공 포털
    "nicebizinfo.com", "nicednb.com", "creditbank.co.kr", "nts.go.kr",
    "alio.go.kr", "data.go.kr", "ftc.go.kr", "kotra.or.kr", "innobiz.or.kr",
    "kssn.net", "bizno.net", "kepco.co.kr",
)


def _is_blocked_url(url: str) -> bool:
    """url 의 도메인이 BLOCKED_DOMAINS 에 해당하면 True."""
    netloc = urlparse(url).netloc.lower()
    return any(domain in netloc for domain in BLOCKED_DOMAINS)


def load_companies(filepath: str) -> list[str]:
    with open(filepath, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def search_company_url(company_name: str) -> str | None:
    try:
        from googlesearch import search
        queries = [f"{company_name} 공식사이트", f"{company_name} 홈페이지"]
        for query in queries:
            results = list(search(query, num_results=5, lang="ko"))
            for url in results:
                if _is_blocked_url(url):
                    continue
                return url
    except Exception:
        pass

    return _fallback_naver_search(company_name)


def _fallback_naver_search(company_name: str) -> str | None:
    try:
        search_url = f"https://search.naver.com/search.naver?query={requests.utils.quote(company_name + ' 공식 홈페이지')}"
        resp = requests.get(search_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(resp.text, "lxml")

        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href.startswith("http") or _is_blocked_url(href):
                continue
            # 검색엔진 자체 도메인(naver.com / google) 은 회사 URL 이 아니므로 제외
            netloc = urlparse(href).netloc.lower()
            if "naver.com" in netloc or "google" in netloc:
                continue
            return href
    except Exception:
        pass

    return None


STRIP_TAGS = ["script", "style", "noscript", "header", "footer", "nav"]


def _soup_from(resp: requests.Response) -> BeautifulSoup:
    """resp.content(bytes)로 파싱해 BeautifulSoup이 meta charset을 자동감지하게 한다.
    resp.text는 charset 헤더가 없는 한글 사이트에서 ISO-8859-1로 오판해 한글이 깨진다."""
    return BeautifulSoup(resp.content, "lxml")


def _extract_text(soup: BeautifulSoup) -> str:
    """nav/script 등을 제거하고 공백 정규화한 본문 텍스트 반환."""
    for tag in soup(STRIP_TAGS):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_text(url: str) -> str:
    """url을 받아 정상 인코딩으로 디코딩한 본문 텍스트 반환. 실패 시 빈 문자열."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        return _extract_text(_soup_from(resp))
    except Exception:
        return ""


def crawl_page(url: str) -> tuple[str, str, str]:
    """(본문 텍스트, 최종 URL, 실패사유) 반환. 성공 시 실패사유는 빈 문자열."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = _soup_from(resp)

        # nav/header 제거 전에 링크 먼저 수집 (nav 안에 링크가 있는 경우 대비)
        sub_url = _find_subpage(soup, url)
        ceo_url = _find_ceo_page(soup, url)

        parts = [_extract_text(soup)]
        if sub_url:
            parts.append(_fetch_text(sub_url))
        if ceo_url and ceo_url != sub_url:
            parts.append(_fetch_text(ceo_url))

        text = " ".join(p for p in parts if p).strip()
        final_text = text[:MAX_TEXT_LENGTH]
        reason = "" if final_text else "빈 페이지(본문 텍스트 없음, JS 렌더링 가능성)"
        return final_text, url, reason
    except Exception as e:
        return "", url, f"접속 실패({type(e).__name__})"


def _find_subpage(soup: BeautifulSoup, base_url: str) -> str | None:
    keywords = ["about", "회사소개", "company", "채용", "인재", "culture"]
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if any(kw in href.lower() or kw in text for kw in keywords):
            if href.startswith("http"):
                return href
            if href.startswith("/"):
                parsed = urlparse(base_url)
                return f"{parsed.scheme}://{parsed.netloc}{href}"
    return None


def _find_ceo_page(soup: BeautifulSoup, base_url: str) -> str | None:
    ceo_keywords = ["ceo", "message", "대표이사", "인삿말"]
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if any(kw in href.lower() or kw in text for kw in ceo_keywords):
            if href.startswith("http"):
                return href
            if href.startswith("/"):
                parsed = urlparse(base_url)
                return f"{parsed.scheme}://{parsed.netloc}{href}"
    return None


SYSTEM_PROMPT = """당신은 기업 정보를 분석하는 전문가입니다.
주어진 텍스트에서 다음 정보를 추출하여 반드시 JSON 형식으로만 응답하세요.
다른 설명이나 마크다운 없이 순수 JSON만 출력하세요.
정보가 없으면 null로 표시하세요.

중요: 웹페이지 텍스트가 요청한 회사가 아니라 채용 포털(잡코리아·게임잡 등),
기업정보/신용조회 사이트(나이스평가정보 등), 정부기관, 또는 전혀 다른 회사에 관한
내용이라면 is_company_match 를 false 로 설정하세요. 텍스트가 명확히 해당 회사
자신의 소개일 때만 true 로 설정합니다."""

USER_PROMPT_TEMPLATE = """회사명: {company_name}
웹페이지 텍스트:
{webpage_text}

위 내용을 분석하여 아래 JSON 형식으로 추출해줘:
{{
  "company_name": "회사명",
  "website_url": "공식 홈페이지 URL",
  "business_description": "주요 사업 내용 (2~3문장)",
  "main_products_services": ["주요 제품/서비스 1", "주요 서비스 2"],
  "talent_values": "인재상 (없으면 null)",
  "ceo_message": "CEO 인삿말 요약 (없으면 null)",
  "is_company_match": true,
  "crawl_success": true
}}"""


def extract_info_with_openai(company_name: str, webpage_text: str, website_url: str) -> dict:
    user_prompt = USER_PROMPT_TEMPLATE.format(
        company_name=company_name,
        webpage_text=webpage_text,
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    result = _normalize_nulls(json.loads(raw))
    result["website_url"] = result.get("website_url") or website_url
    return result


def _normalize_nulls(result: dict) -> dict:
    """LLM 이 빈 값을 문자열 'null'/'none'/'' 로 내보내는 경우를 실제 None 으로 변환."""
    def clean(value):
        if isinstance(value, str) and value.strip().lower() in ("null", "none", ""):
            return None
        if isinstance(value, list):
            return [v for v in value if clean(v) is not None]
        return value

    return {key: clean(value) for key, value in result.items()}


def _normalize_name(s: str) -> str:
    """회사명/본문 비교용 정규화: 법인 접미사·괄호·공백 제거 후 소문자화."""
    return re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\s+", "", s or "").lower()


def name_appears_in_text(company_name: str, text: str) -> bool:
    """본문에 회사명이 실제로 등장하는지 확인.
    풀네임이 없으면 회사명 앞쪽 한글 2글자(브랜드명)라도 있으면 일치로 본다.
    (예: '지산소프트' 사이트가 브랜드 '지산'만 노출하는 경우 대응)"""
    normalized_text = _normalize_name(text)
    normalized_name = _normalize_name(company_name)
    if normalized_name and normalized_name in normalized_text:
        return True
    prefix_match = re.match(r"[가-힣]{2,}", company_name)
    prefix = prefix_match.group(0)[:2] if prefix_match else ""
    return bool(prefix) and prefix in normalized_text


def _failure(company_name: str, website_url: str | None, error: str) -> dict:
    """실패 결과 dict 생성. error 에 구체적 사유를 담는다."""
    return {
        "company_name": company_name,
        "website_url": website_url,
        "business_description": None,
        "main_products_services": [],
        "talent_values": None,
        "ceo_message": None,
        "crawl_success": False,
        "error": error,
    }


def process_company(company_name: str, index: int, total: int) -> dict:
    print(f"[{index}/{total}] {company_name} 처리 중...", flush=True)

    url = search_company_url(company_name)
    if url:
        parsed = urlparse(url)
        url = f"{parsed.scheme}://{parsed.netloc}"
    if not url:
        print(f"  → URL 탐색 실패, 크롤링 건너뜀")
        return _failure(company_name, None, "URL 탐색 실패: 검색 결과에서 공식 사이트를 찾지 못함")

    print(f"  → URL: {url}")
    text, final_url, fail_reason = crawl_page(url)

    if not text:
        print(f"  → 크롤링 실패: {fail_reason}")
        return _failure(company_name, final_url, fail_reason or "페이지 크롤링 실패")

    try:
        result = extract_info_with_openai(company_name, text, final_url)
    except json.JSONDecodeError as e:
        print(f"  → JSON 파싱 오류: {e}")
        return _failure(company_name, final_url, f"JSON 파싱 실패: {e}")
    except Exception as e:
        print(f"  → OpenAI 오류: {e}")
        return _failure(company_name, final_url, f"OpenAI 오류: {e}")

    if result.get("is_company_match") is False:
        print(f"  → 이름 매칭 실패(포털/타사 페이지)")
        return _failure(company_name, final_url, "이름 매칭 실패: 페이지가 요청 회사가 아닌 포털/타사 내용")

    if not name_appears_in_text(company_name, text):
        print(f"  → 이름 미확인(본문에 회사명 없음)")
        return _failure(company_name, final_url, "이름 미확인: 본문에 회사명이 없어 동일 회사인지 확인 불가")

    description = (result.get("business_description") or "").strip()
    products = result.get("main_products_services") or []
    if not description and not products:
        print(f"  → 내용 없음(추출 실패)")
        return _failure(company_name, final_url, "내용 없음: 사이트는 맞으나 사업 정보를 추출하지 못함(빈/JS 페이지 가능성)")

    result.pop("is_company_match", None)
    result["crawl_success"] = True
    print(f"  → 완료")
    return result


CSV_FIELDS = [
    "company_name",
    "website_url",
    "business_description",
    "main_products_services",
    "talent_values",
    "ceo_message",
    "crawl_success",
    "error",
]


def append_csv_row(filepath: str, result: dict, write_header: bool) -> None:
    row = {**result, "main_products_services": "|".join(result.get("main_products_services") or [])}
    with open(filepath, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def main():
    companies = load_companies("companies.txt")
    total = len(companies)
    print(f"총 {total}개 회사 처리 시작\n")

    output_path = "output/results.csv"
    success_count = 0

    for i, company in enumerate(companies, start=1):
        result = process_company(company, i, total)
        append_csv_row(output_path, result, write_header=(i == 1))
        if result.get("crawl_success"):
            success_count += 1

        if i < total:
            time.sleep(DELAY_BETWEEN_COMPANIES)

    print(f"\n완료! 결과 저장: {output_path}")
    print(f"성공: {success_count}/{total}")


if __name__ == "__main__":
    main()
