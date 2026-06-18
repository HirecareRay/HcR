"""환경 설정과 상수, OpenAI 클라이언트 지연 초기화."""
import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

MODEL = "gpt-4o-mini"
TEMPERATURE = 0  # 결정적 출력으로 할루시네이션 억제

MAX_TEXT_LENGTH = 6000
REQUEST_TIMEOUT = 10
DELAY_BETWEEN_COMPANIES = 2
MAX_URL_CANDIDATES = 5  # 검증을 시도할 후보 URL 최대 개수
MAX_REFERENCE_URLS = 5  # 외부 참고 URL 최대 개수

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

# 참고 출처(폴백) 우선순위: 인재상이 서버 HTML로 잘 노출돼 크롤이 안정적인 순.
# 검색 site: 필터와 후보 정렬에 함께 쓴다. 앞쪽일수록 우선 시도한다.
REFERENCE_JOBSITES = (
    "catch.co.kr", "jobkorea.co.kr", "saramin.co.kr",
    "incruit.com", "jobplanet.co.kr", "wanted.co.kr",
)

# 공식 홈페이지가 아닌 것으로 간주해 후보에서 제외할 도메인
BLOCKED_DOMAINS = (
    "linkedin.com", "jobkorea.co.kr", "saramin.co.kr", "wanted.co.kr",
    "incruit.com", "jobplanet.co.kr", "catch.co.kr", "rocketpunch.com",
    "google.", "naver.com", "daum.net", "youtube.com", "facebook.com",
    "instagram.com", "blog.", "tistory.com", "namu.wiki", "wikipedia.org",
    "newswire.co.kr", "yna.co.kr",
)


@lru_cache(maxsize=1)
def get_client():
    """OpenAI 클라이언트를 지연 생성한다(키 없으면 즉시 오류)."""
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY가 .env 파일에 설정되지 않았습니다.")
    return OpenAI(api_key=api_key)
