import csv
import os
import random
import re
import time
from datetime import datetime, timedelta
import urllib.parse
from bs4 import BeautifulSoup
import requests
from urllib.parse import urlparse, parse_qs
from glob import glob

def clean_text(text):
    # 1. 괄호와 괄호 안의 모든 내용 제거 (소괄호, 중괄호, 대괄호 포함)
    text = re.sub(r'\([^)]*\)|\[[^\]]*\]|\{[^}]*\}', '', text)
    # 2. 한글, 영문, 숫자, 공백을 제외한 모든 특수문자 제거
    text = re.sub(r'[^가-힣a-zA-Z0-9\s]', '', text)
    # 3. 연속된 공백을 하나로 줄이고 앞뒤 공백 제거
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

SAVE_DIR = "news"
ERROR_DIR = "news_error"
def clean_filename(name):
    return re.sub(r'[\\/:*?"<>|\t\n\r]', '_', name)
def full_path(filename: str) -> str:
    return os.path.join(os.path.dirname(__file__), filename)
os.makedirs(full_path(SAVE_DIR), exist_ok=True)
os.makedirs(full_path(ERROR_DIR), exist_ok=True)

def save_to_csv(page_data, tsv_file):
    tsv_file = full_path(os.path.join(SAVE_DIR, tsv_file))
    file_exists = os.path.isfile(tsv_file)

    with open(tsv_file, "a+", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["company", "title", "url", "press", "write_date"]
        company = os.path.basename(tsv_file).split("_from_")[0]  # 파일명에서 기업명 추출
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        if not file_exists:
            writer.writeheader()

        # 수집된 건 누적 파일 쓰기
        for news in page_data:
            writer.writerow(
                {
                    "company": company.replace("0total_", ""),  # 파일명에서 기업명 추출
                    "title": news["title"],
                    "url": news["url"],
                    "press": news["press"],
                    "write_date": news["write_date"],
                }
            )

def clean_naver_url(raw_url):
    """필수 파라미터만 남기고 나머지 쓰레기 쿼리 제거"""
    parsed = urlparse(raw_url)
    params = parse_qs(parsed.query)
    
    # 필수 식별값인 기사 ID와 언론사 ID만 추출
    clean_params = {
        "article_id": params.get("article_id", [""])[0],
        "office_id": params.get("office_id", [""])[0]
    }
    
    # 필요한 값만 조립하여 깔끔한 주소로 반환
    return f"https://n.news.naver.com/mnews/article/{clean_params['office_id']}/{clean_params['article_id']}"

# 1. 사람처럼 보이기 위한 이상적인 HTTP 헤더 설정
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/news/",
    # "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    # "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
}

def get_euc_kr_url_query(keyword):
    """네이버 금융 전용 EUC-KR 인코딩 검색어 쿼리스트링 생성"""
    return urllib.parse.quote(keyword.encode("euc-kr"))

def get_search_count(keyword, start_date, end_date, sm):
    """특정 기간 동안의 검색 결과 기사 총 개수를 반환"""
    encoded_q = get_euc_kr_url_query(keyword)
    url = f"https://finance.naver.com/news/news_search.naver?rcdate=1&q=%22{encoded_q}%22&sm={sm}.basic&pd=4&stDateStart={start_date}&stDateEnd={end_date}"

    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code != 200:
            time.sleep(random.uniform(0.5, 1.2))  # 디도스 방지 랜덤 딜레이
            res = requests.get(url, headers=HEADERS, timeout=200)
            if res.status_code != 200:
                return 0

        soup = BeautifulSoup(res.text, "html.parser")
        count_tag = soup.select_one("p.resultCount strong:nth-of-type(2)")
        if count_tag:
            # 쉼표 제거 후 숫자로 변환 (예: "1,107" -> 1107)
            return int(count_tag.text.replace(",", "").strip())
    except Exception as e:
        print(f"[{keyword}] 개수 조회 중 오류 발생 ({start_date} ~ {end_date}): {e}")
    return 0


def collect_page_data(keyword, start_date, end_date, sm):
    """조건 3, 4: 주어진 기간 내의 모든 페이지를 순회하며 뉴스 데이터를 수집"""
    encoded_q = get_euc_kr_url_query(keyword)
    page = 1
    collected_items = []

    while True:
        url = f"https://finance.naver.com/news/news_search.naver?rcdate=1&q=%22{encoded_q}%22&sm={sm}.basic&pd=4&stDateStart={start_date}&stDateEnd={end_date}&page={page}"

        res = requests.get(url, headers=HEADERS, timeout=100)
        if res.status_code != 200:
            time.sleep(random.uniform(1,2, 3.9))  # 디도스 방지 랜덤 딜레이
            continue
        soup = BeautifulSoup(res.text, "html.parser")
        news_list = soup.select("dl.newsList")

        # 해당 페이지에 뉴스 데이터가 없으면 중단 (마지막 페이지 초과)
        if not news_list:
            break

        for dl in news_list:
            subject_tags = dl.select(".articleSubject a")
            press_tags = dl.select(".articleSummary .press")
            wdate_tags = dl.select(".articleSummary .wdate")
            
            for subject_tag, press_tag, wdate_tag in zip(subject_tags, press_tags, wdate_tags):
                if subject_tag:
                    title = subject_tag.text.strip()
                    href = urllib.parse.urljoin(url, subject_tag["href"])
                    press = press_tag.text.strip() if press_tag else ""
                    write_date = wdate_tag.text.strip().split()[0] if wdate_tag else ""

                    collected_items.append(
                        {
                            "url": clean_naver_url(href),
                            "title": title,
                            "press": press,
                            "write_date": write_date,
                        }
                    )


        print(f"  -> [{keyword}] {start_date} ~ {end_date} | {page} 페이지 수집 완료 ({len(collected_items)}건 수집중)")
        # 비어있는 페이지거나 다음 페이지 데이터가 없으면 무한루프 탈출
        if not soup.select_one(".pgR") and not soup.select_one(".pgRR"):
            print(res.url)
            break
        page += 1

    return collected_items


def scrape_corporate_news(keyword, target_start_date="2024-01-01"):
    """조건 1, 2: 기업명을 입력받아 조건에 맞게 기간을 쪼개며 모든 데이터를 수집"""
    print(f"\n==================== [{keyword}] 수집 시작 ====================")
    # 2026-06-14 기준 시스템의 오늘 날짜로 종료일 설정
    # current_end_date = datetime.strptime(datetime.today(), "%Y-%m-%d")
    current_end_date = datetime.strptime(datetime.today().strftime("%Y-%m-%d"), "%Y-%m-%d")
    limit_start_date = datetime.strptime("1997-01-01", "%Y-%m-%d")

    all_data_cnt = 0

    test_start_date = limit_start_date.strftime("%Y-%m-%d")
    end_str = current_end_date.strftime("%Y-%m-%d")

    # 1차 전체 기간 조회
    sm = "all"
    total_count = get_search_count(keyword, test_start_date, end_str, sm)
    print(f"[*] 전체 조회 구간 ({test_start_date} ~ {end_str}) -> 검색 결과: {total_count}건")

    # 기사 건수가 2000건보다 낮으면 전체 구간을 한번에 수집하고 루프 종결
    if total_count < 2000:
        page_data = collect_page_data(keyword, test_start_date, end_str, sm)
        save_to_csv(page_data, f"0total_{keyword}_from_{test_start_date}_to_{end_str}.tsv")
        all_data_cnt += len(page_data)
        limit_start_date = current_end_date + timedelta(days=30)
    else:
        limit_start_date = datetime.strptime(target_start_date, "%Y-%m-%d")
        test_start_date = limit_start_date.strftime("%Y-%m-%d")
        total_count = get_search_count(keyword, test_start_date, end_str, sm)
        total_tsv_file = f"0total_{keyword}_from_{test_start_date}_to_{end_str}.tsv"
        print(f"[*] 3개년 임시 조회 구간 ({test_start_date} ~ {end_str}) -> 검색 결과: {total_count}건")
        if total_count < 2000:
            print(f"[!] 확정된 스케일링 구간 수집: {test_start_date} ~ {end_str}")
            page_data = collect_page_data(keyword, test_start_date, end_str, sm)
            save_to_csv(page_data, total_tsv_file)
            all_data_cnt += len(page_data)
            limit_start_date = current_end_date + timedelta(days=30)

    # 최초 3년(혹은 전체 기간) 조회를 위해 무조건 과거 타겟 날짜를 시작일로 지정해 검사
    test_start_date = limit_start_date.strftime("%Y-%m-%d")
    end_str = current_end_date.strftime("%Y-%m-%d")
    sm = "title"
    # 2000건 이상일 때: 기간 단위를 낮춰가며 검색 결과를 2000건 미만으로 떨어뜨림
    intervals = [
        ("year", 365),
        ("month", 30),
        ("week", 7),
        ("day", 1),
    ]  # 1달(30일), 1주(7일), 1일 단위 조절
    intervals_reverse = list(reversed(intervals))[1:]
    chosen_days = 365

    while current_end_date >= limit_start_date:
        temp_start = current_end_date - timedelta(days=chosen_days)
        start_str = temp_start.strftime("%Y-%m-%d")
        end_str = current_end_date.strftime("%Y-%m-%d")
        count = get_search_count(keyword, start_str, end_str, sm)
        if count < 2000:
            for unit_name, days in intervals_reverse:
                temp_start = current_end_date - timedelta(days=days)
                if temp_start < limit_start_date:
                    temp_start = limit_start_date

                start_str = temp_start.strftime("%Y-%m-%d")
                count = get_search_count(keyword, start_str, end_str, sm)

                if count < 2000:
                    chosen_days = days
                else:
                    break
        else:
            for unit_name, days in intervals:
                temp_start = current_end_date - timedelta(days=days)
                if temp_start < limit_start_date:
                    temp_start = limit_start_date

                start_str = temp_start.strftime("%Y-%m-%d")
                count = get_search_count(keyword, start_str, end_str, sm)

                # 2000건 미만으로 떨어지거나 최후 보루인 1일 단위가 되면 이 구간 채택
                if count < 2000 or unit_name == "day":
                    chosen_days = days
                    break

        # 결정된 최적의 구간 계산 및 수집
        final_start_date = current_end_date - timedelta(days=chosen_days)
        if final_start_date < limit_start_date:
            final_start_date = limit_start_date

        f_start_str = final_start_date.strftime("%Y-%m-%d")
        f_end_str = current_end_date.strftime("%Y-%m-%d")

        print(f"[!] 확정된 스케일링 구간 수집: {f_start_str} ~ {f_end_str}")
        page_data = collect_page_data(keyword, f_start_str, f_end_str, sm)
        save_to_csv(page_data, total_tsv_file)
        all_data_cnt += len(page_data)

        # 다음 차수를 위해 현재 종료일을 다음 날짜 구간 직전(하루 전)으로 이동
        current_end_date = final_start_date - timedelta(days=1)
            
        # 결과 저장 공간 파일 생성
        tsv_file = f"{keyword}_from_{f_start_str}_to_{f_end_str}.tsv"
        save_to_csv(page_data, tsv_file)
    if all_data_cnt == 0:
        with open(full_path(f"{ERROR_DIR}\\0_companies_headless.tsv"), "a+", encoding="utf-8") as f:
            f.writelines(keyword + "\n")
            
    print(f"[✔] [{keyword}] 최종 수집 완료! 총 건수: {all_data_cnt}건")

# ==================== 실행 예시 ====================
if __name__ == "__main__":
    corporate_list = []
    for fileName in glob(full_path("data/*.tsv")):
        with open(fileName, "r", encoding="utf_8") as f:
            csv_reader = csv.reader(f, delimiter="\t")
            for row in csv_reader:
                corporate_list.append(row[0])
    
    corporate_list = [clean_text(company) for company in corporate_list]
    corporate_list = list(set(corporate_list))
    corporate_list.sort()
    print(len(corporate_list))
    for corp in corporate_list:
        corp_news = scrape_corporate_news(clean_filename(corp), target_start_date="2024-01-01")