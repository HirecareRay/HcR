import csv
import json
import os
import pandas as pd
import scrapy
from glob import glob
from scrapy.crawler import CrawlerProcess
from pathlib import Path

# Path 객체 생성 후 str()로 감싸기
BASE_DIR = str(Path(__file__).resolve().parents[3])
COMPANY = "넷마블"

class NaverNewsSpider(scrapy.Spider):
    name = 'naver_fast_spider'
    # 1. 수만 개의 URL이 담긴 여러 개의 tsv 파일 로드 및 결합
    async def start(self):
        # 현재 디렉토리 및 하위 폴더의 모든 .tsv 파일을 찾음
        # print(f"✅ 파싱 성공: {response.url}")
        # tsv_files = glob.glob(f"{BASE_DIR}\\news\\*.tsv")  # 필요 시 경로 변경 예: "./data/*.tsv"
        # tsv_files = glob(f"{BASE_DIR}\\news\\삼화왕관*.tsv") + glob(f"{BASE_DIR}\\news\\CJ ENM*.tsv")  # 필요 시 경로 변경 예: "./data/*.tsv"
        tsv_files = glob(f"{BASE_DIR}\\news\\{COMPANY}*total*.tsv")
        # tsv_files = glob(f"{BASE_DIR}\\news\\삼성전자*total*.tsv")
        # tsv_files = [r"C:\myfolder\spc\scp_genai\tmp_xray_model\news\CJ ENM_from_2024-01-01_to_2024-06-12.tsv"]
        if not tsv_files:
            self.logger.error("❌ 실행 실패: tsv 파일을 찾을 수 없습니다. 경로를 확인하세요.")
            return

        self.logger.info(f"📂 발견된 tsv 파일 수: {len(tsv_files)}개. URL 로딩 시작...")
        
        # 모든 tsv의 URL 취합 (중복 제거로 효율성 극대화)
        for file in tsv_files:
            
            base_name = os.path.basename(file).split(".")[0]
            urls = set()
            try:
                # tsv 파일에서 'url' 컬럼 추출 (컬럼명이 다르면 수정 필요)
                df = pd.read_csv(file, sep='\t', usecols=['url'])
                urls.update(df['url'].dropna().tolist())
            except Exception as e:
                self.logger.error(f"⚠️ 파일 읽기 오류 ({file}): {e}")
        
            self.logger.info(f"🚀 총 {len(urls):,}개의 유니크 URL 수집 시작!")
            
            # 고속 처리를 위해 필수적인 한국어 브라우저 헤더 세팅
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
            }
            for url in urls:
                # if "n.news.naver.com" in url:  # 네이버 뉴스 URL 검증
                    yield scrapy.Request(url=url, headers=headers, callback=self.parse, cb_kwargs={"base_name": base_name, "file": file})

    # 2. 고속 파싱 (Scrapy 자체 Selector는 lxml 기반이라 BeautifulSoup보다 훨씬 빠름)
    def parse(self, response, base_name, file, company):
        print(f"✅ 파싱 성공: {response.url}")
        try:
            title = response.css('#title_area > span ::text').get()
            body = response.css('article#dic_area ::text, #articleBodyContents ::text').getall()
            media = response.css('a.media_end_head_top_logo img::attr(alt)').get()

            clean_body = " ".join([text.strip() for text in body if text.strip()])
            
            # 저장할 데이터 딕셔너리 생성
            item = {
                'url': response.url,
                'title': title.strip() if title else '',
                'media': media.strip() if media else '',
                'body': clean_body
            }

            # 💡 [StackOverflow 핵심 로직]: 
            # item 데이터를 기반으로 dynamic하게 파일명을 정의합니다. (회사명 기준)
            output_file_name = f"article/{company}_scraped_news_result.jsonl"
            
            # 실시간으로 수집되는 데이터를 즉시 파일에 추가 기록 (jsonlines 포맷)
            with open(output_file_name, "a+", encoding="utf-8") as f:
                line = json.dumps(item, ensure_ascii=False) + "\n"
                f.write(line)

            self.logger.info(f"✅ [{company}] 파일 기록 성공: {response.url}")
            
        except Exception as e:
            self.logger.error(f"❌ 파싱 에러 ({response.url}): {e}")
            error_file = f"{base_name}_errors.csv"
            file_exists = os.path.exists(error_file)

            with open(error_file, "a+", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["base_name", "url", "file_name"])
                writer.writerow([base_name, response.url, file])

# 3. 스크립트 실행 제어부
if __name__ == "__main__":
    # 고속 스크래핑을 위한 최적화 세팅 주입
    # tsv_files = glob(f"{BASE_DIR}\\news\\*total*.tsv")
    # tsv_files = glob(f"{BASE_DIR}\\news\\삼화왕관*.tsv") + glob(f"{BASE_DIR}\\news\\CJ ENM*.tsv")  # 필요 시 경로 변경 예: "./data/*.tsv"

    settings = {
        "ROBOTSTXT_OBEY": False,              # robots.txt 무시 (법적 문제 없는 범위 내에서)

        'CONCURRENT_REQUESTS': 64,          # 동시 요청 수 최고 수준으로 끌어올림 (기본값 16)
        'CONCURRENT_REQUESTS_PER_DOMAIN': 64, # 네이버 도메인 하나만 조지므로 동일하게 설정
        # 'DOWNLOAD_DELAY': 1.5,                # 요청 간 기본 1.5초 대기
        # 'RANDOMIZE_DOWNLOAD_DELAY': True,     # 0.5배 ~ 1.5배 사이로 랜덤 대기 (로봇 탐지 우회)
        'AUTOTHROTTLE_ENABLED': True,
        'AUTOTHROTTLE_START_DELAY': 1.5,
        'AUTOTHROTTLE_MAX_DELAY': 10.0,

        'DOWNLOAD_TIMEOUT': 15,

        'COOKIES_ENABLED': False,           # 쿠키 비활성화
        'LOG_LEVEL': 'INFO',                # 수만 개 처리 시 로그가 너무 많아지므로 INFO로 제한
        # 'LOG_FILE': f'{COMPANY}_error.log',
        # 'LOG_LEVEL': 'ERROR',
        'RETRY_TIMES': 2,                   # 실패 시 재시도 횟수
        'FEED_EXPORT_ENCODING': 'utf-8',    # 한글 깨짐 방지
        # 실시간으로 수집되는 데이터를 파일에 즉시 기록 (메모리 폭발 방지)
        # 'FEEDS': {
        #     # 'scraped_news_result.jsonl': {   # JSON Lines 포맷 (줄바꿈 기준 JSON이라 대용량에 가장 안정적)
        #     f'{COMPANY}_scraped_news_result.json': {   # JSON Lines 포맷 (줄바꿈 기준 JSON이라 대용량에 가장 안정적)
        #         'format': 'jsonlines',
        #         'overwrite': True,
        #     }
        # }
    }

    process = CrawlerProcess(settings)
    process.crawl(NaverNewsSpider)
    process.start() # 크롤링 시작
    print(f"✅ 크롤링 완료! 결과는 {COMPANY}_scraped_news_result.json 파일에 저장되었습니다.")
