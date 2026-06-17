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


def load_companies(filepath: str) -> list[str]:
    with open(filepath, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def search_company_url(company_name: str) -> str | None:
    try:
        from googlesearch import search
        queries = [f"{company_name} 공식사이트", f"{company_name} 홈페이지"]
        for query in queries:
            results = list(search(query, num_results=3, lang="ko"))
            for url in results:
                if any(blocked in url for blocked in ["linkedin", "jobkorea", "saramin", "naver.com/search", "google"]):
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
            if href.startswith("http") and "naver" not in href and "google" not in href:
                return href
    except Exception:
        pass

    return None


def crawl_page(url: str) -> tuple[str, str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # nav/header 제거 전에 링크 먼저 수집 (nav 안에 링크가 있는 경우 대비)
        sub_url = _find_subpage(soup, url)
        ceo_url = _find_ceo_page(soup, url)

        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()

        if sub_url:
            try:
                sub_resp = requests.get(sub_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                sub_soup = BeautifulSoup(sub_resp.text, "lxml")
                for tag in sub_soup(["script", "style", "noscript", "header", "footer", "nav"]):
                    tag.decompose()
                sub_text = sub_soup.get_text(separator=" ", strip=True)
                sub_text = re.sub(r"\s+", " ", sub_text).strip()
                text = text + " " + sub_text
            except Exception:
                pass

        if ceo_url and ceo_url != sub_url:
            try:
                ceo_resp = requests.get(ceo_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                ceo_soup = BeautifulSoup(ceo_resp.text, "lxml")
                for tag in ceo_soup(["script", "style", "noscript", "header", "footer", "nav"]):
                    tag.decompose()
                ceo_text = ceo_soup.get_text(separator=" ", strip=True)
                ceo_text = re.sub(r"\s+", " ", ceo_text).strip()
                text = text + " " + ceo_text
            except Exception:
                pass

        return text[:MAX_TEXT_LENGTH], url
    except Exception as e:
        return "", url


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
정보가 없으면 null로 표시하세요."""

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

    result = json.loads(raw)
    result["website_url"] = result.get("website_url") or website_url
    return result


def process_company(company_name: str, index: int, total: int) -> dict:
    print(f"[{index}/{total}] {company_name} 처리 중...", flush=True)

    url = search_company_url(company_name)
    if url:
        parsed = urlparse(url)
        url = f"{parsed.scheme}://{parsed.netloc}"
    if not url:
        print(f"  → URL 탐색 실패, 크롤링 건너뜀")
        return {
            "company_name": company_name,
            "website_url": None,
            "business_description": None,
            "main_products_services": [],
            "talent_values": None,
            "ceo_message": None,
            "crawl_success": False,
            "error": "URL을 찾을 수 없음",
        }

    print(f"  → URL: {url}")
    text, final_url = crawl_page(url)

    if not text:
        print(f"  → 크롤링 실패")
        return {
            "company_name": company_name,
            "website_url": final_url,
            "business_description": None,
            "main_products_services": [],
            "talent_values": None,
            "ceo_message": None,
            "crawl_success": False,
            "error": "페이지 크롤링 실패",
        }

    try:
        result = extract_info_with_openai(company_name, text, final_url)
        result["crawl_success"] = True
        print(f"  → 완료")
        return result
    except json.JSONDecodeError as e:
        print(f"  → JSON 파싱 오류: {e}")
        return {
            "company_name": company_name,
            "website_url": final_url,
            "business_description": None,
            "main_products_services": [],
            "talent_values": None,
            "ceo_message": None,
            "crawl_success": False,
            "error": f"JSON 파싱 실패: {e}",
        }
    except Exception as e:
        print(f"  → OpenAI 오류: {e}")
        return {
            "company_name": company_name,
            "website_url": final_url,
            "business_description": None,
            "main_products_services": [],
            "talent_values": None,
            "ceo_message": None,
            "crawl_success": False,
            "error": str(e),
        }


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
