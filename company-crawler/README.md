# 기업 정보 자동 수집기 (Company Crawler)

기업 이름 목록을 입력하면, 각 회사의 공식 홈페이지를 자동으로 찾아 핵심 정보를 수집하고 CSV 파일로 저장하는 Python 스크립트입니다.

---

## 어떻게 동작하나요?

```
companies.txt (회사 이름 목록)
        ↓
① 구글/네이버 검색으로 공식 홈페이지 URL 탐색
        ↓
② 홈페이지 크롤링 (회사소개, CEO 인삿말 페이지 포함)
        ↓
③ OpenAI GPT-4o-mini로 핵심 정보 추출
        ↓
output/results.csv (결과 저장)
```

---

## 수집하는 정보

| 항목 | 설명 |
|------|------|
| `company_name` | 회사명 |
| `website_url` | 공식 홈페이지 주소 |
| `business_description` | 주요 사업 내용 (2~3문장) |
| `main_products_services` | 주요 제품 / 서비스 목록 |
| `talent_values` | 인재상 |
| `ceo_message` | CEO 인삿말 요약 |
| `crawl_success` | 수집 성공 여부 (True/False) |
| `error` | 실패 시 오류 메시지 |

---

## 프로젝트 구조

```
company-crawler/
├── main.py           # 메인 스크립트
├── companies.txt     # 크롤링할 회사 목록 (784개)
├── requirements.txt  # 필요한 Python 패키지
├── .env              # API 키 설정 (git에 포함 안 됨)
└── output/
    └── results.csv   # 수집 결과
```

---

## 설치 및 실행

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. API 키 설정

`.env` 파일을 만들고 OpenAI API 키를 입력합니다.

```
OPENAI_API_KEY=sk-...
```

### 3. 회사 목록 준비

`companies.txt`에 회사 이름을 한 줄에 하나씩 입력합니다.

```
삼성전자
카카오
네이버
```

### 4. 실행

```bash
python main.py
```

실행하면 아래처럼 진행 상황이 출력됩니다.

```
총 784개 회사 처리 시작

[1/784] CJ ENM 처리 중...
  → URL: https://www.cjenm.com
  → 완료
[2/784] CJ대한통운 처리 중...
  ...

완료! 결과 저장: output/results.csv
성공: 720/784
```

---

## 주요 동작 설명

### URL 탐색 방식
1. **Google 검색** - `{회사명} 공식사이트` 키워드로 검색
2. **네이버 검색 (Fallback)** - Google 실패 시 네이버로 대체
3. LinkedIn, 잡코리아, 사람인 같은 채용 사이트는 자동으로 제외

### 크롤링 방식
- 메인 홈페이지 크롤링 후, **회사소개/About/채용** 페이지와 **CEO 인삿말** 페이지를 추가로 수집
- 광고, 스크립트, 메뉴 등 불필요한 요소는 자동 제거

### AI 정보 추출
- 수집된 텍스트를 **GPT-4o-mini**에 전달
- 미리 정의된 JSON 형식으로 구조화된 정보 추출
- 정보가 없는 항목은 `null`로 표시

---

## 현재 처리 대상

`companies.txt`에 **784개** 회사가 등록되어 있습니다. (주요 IT/스타트업/대기업 포함)

---

## 사용 라이브러리

| 라이브러리 | 역할 |
|-----------|------|
| `openai` | GPT-4o-mini로 텍스트에서 정보 추출 |
| `requests` | 웹페이지 HTTP 요청 |
| `beautifulsoup4` | HTML 파싱 및 텍스트 추출 |
| `googlesearch-python` | 구글 검색으로 URL 탐색 |
| `lxml` | HTML 파서 (beautifulsoup4와 함께 사용) |
| `python-dotenv` | `.env` 파일에서 환경 변수 로드 |
