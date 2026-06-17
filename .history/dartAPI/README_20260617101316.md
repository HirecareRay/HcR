# DART 데이터 수집기

금융감독원 **전자공시시스템(DART)** Open API를 활용해 상장기업의 공시 데이터를 수집하고 SQLite DB에 저장하는 파이썬 프로젝트입니다.

---

## 전체 구조

```
dart/
├── config.py        # API 키 설정 (.env에서 로드)
├── corp_code.py     # 기업 고유번호 목록 다운로드 & 캐시
├── dart_api.py      # DART API 호출 함수 7개
├── dart_report.py   # 사업보고서 원문 XML 파싱
├── db.py            # SQLite DB 초기화 & 저장 함수
├── collect.py       # 메인 실행 파일 (파이프라인 오케스트레이터)
├── .env             # DART_API_KEY 저장 (git 제외)
├── .gitignore
├── README.md
└── data/
    ├── corp_codes.csv   # 전체 기업 고유번호 캐시 파일
    └── dart.db          # SQLite 데이터베이스
```

---

## 파일별 역할

### `config.py` — API 키 관리
- `.env` 파일에서 `DART_API_KEY`를 읽어 환경변수로 등록
- 키가 없으면 즉시 에러를 발생시켜 잘못된 실행 방지

---

### `corp_code.py` — 기업 고유번호 관리

DART에 등록된 모든 기업의 8자리 고유번호를 관리합니다.

| 함수 | 설명 |
|------|------|
| `load_corp_codes()` | 전체 기업 목록 반환. CSV 캐시가 있으면 파일에서 읽고, 없으면 API에서 다운로드 |
| `get_corp_code("삼성전자")` | 회사명 → 8자리 고유번호 변환 |

**흐름:**
```
DART API (corpCode.xml zip)
    ↓ 압축 해제 + XML 파싱
    ↓ CSV로 캐시 저장
data/corp_codes.csv
```

---

### `dart_api.py` — DART API 호출

`corp_code`(기업 고유번호)를 받아 다양한 정보를 조회하는 함수 7개를 제공합니다.

| 함수 | API 엔드포인트 | 조회 내용 |
|------|--------------|-----------|
| `get_company()` | `company.json` | 기업 기본 정보 (이름, 대표자, 주소 등) |
| `get_disclosures()` | `list.json` | 기간별 공시 목록 |
| `get_finance()` | `fnlttSinglAcnt.json` | 재무제표 (매출액, 영업이익 등) |
| `get_employees()` | `empSttus.json` | 직원 현황 (성별, 평균 급여 등) |
| `get_financial_indicators()` | `fnlttSinglIndx.json` | 주요 재무지표 (ROE, 부채비율 등) |
| `get_major_shareholders()` | `hyslrSttus.json` | 최대주주 현황 |
| `get_litigation()` | `lwstLg.json` | 소송 내역 |

> **공통 처리:** 모든 요청은 내부 `_get()` 함수를 거칩니다. DART 응답 `status`가 `"000"`이 아니면 에러 출력 후 `None` 반환. `"013"`(데이터 없음)은 정상 케이스로 처리합니다.

---

### `dart_report.py` — 사업보고서 원문 파싱

DART 사업보고서 ZIP을 내려받아 내부 XML에서 특정 챕터 텍스트를 추출합니다.

**파싱 흐름:**
```
DART document.xml API
    ↓ ZIP 다운로드
    ↓ ZIP 압축 해제 → rcept_no.xml
    ↓ BeautifulSoup으로 <TITLE ATOC="Y"> 태그 탐색
    ↓ 챕터명 매칭 → 부모 <SECTION-N> 추출
    ↓ 불필요 소제목 섹션 제거 (예: 예측정보 주의사항)
    ↓ 최대 10,000자로 자름
```

| 함수 | 설명 |
|------|------|
| `get_latest_report_rcept_no(corp_code)` | 해당 기업의 최신 사업보고서 접수번호 조회 |
| `get_report_text(rcept_no)` | 접수번호로 "사업의 내용", "이사의 경영진단" 텍스트 추출 |

---

### `db.py` — 데이터베이스

SQLAlchemy + SQLite로 데이터를 저장합니다. (추후 MySQL 전환 시 `ENGINE_URL` 한 줄만 변경)

**테이블 구조:**

| 테이블 | 주요 컬럼 | 저장 방식 |
|--------|----------|----------|
| `companies` | corp_code, corp_name, ceo_nm, est_dt, stock_code | upsert (있으면 덮어쓰기) |
| `disclosures` | corp_code, report_nm, rcept_dt, rcept_no, flr_nm | delete → insert |
| `finances` | corp_code, bsns_year, account_nm, fs_div, sj_div, 당기/전기/전전기 금액 | delete → insert |
| `employees` | corp_code, stlm_dt, fo_bbm, sexdstn, sm, 평균급여, 연봉총액 | delete → insert |
| `reports` | corp_code, rcept_no, section_nm, content, created_at | delete → insert |

> **금액 변환:** `"300,870,903,000,000"` → `300870903000000` (정수). `"-"` 또는 빈 값은 `None`으로 저장.

---

### `collect.py` — 메인 실행 파이프라인

회사명 하나를 넣으면 전체 수집 파이프라인을 순서대로 실행합니다.

```
collect("삼성전자")
    │
    ├─ 1. corp_code 변환 (고유번호 조회)
    ├─ 2. 기업 기본 정보 저장
    ├─ 3. 최근 1년 공시 목록 저장
    ├─ 4. 2024년 연결 손익계산서(CFS/IS) 저장
    ├─ 5. 2024년 직원 현황 (성별합계 행) 저장
    └─ 6. 최신 사업보고서 원문 텍스트 저장
              └─ "사업의 내용" + "이사의 경영진단" 챕터
```

---

## 실행 방법

### 1. 환경 설정

```bash
# 의존성 설치
pip install requests sqlalchemy python-dotenv beautifulsoup4

# .env 파일에 API 키 입력
echo "DART_API_KEY=발급받은키" > .env
```

> DART API 키는 [OpenDART](https://opendart.fss.or.kr) 에서 무료로 발급받을 수 있습니다.

### 2. 데이터 수집 실행

```bash
python collect.py
```

기본값으로 **삼성전자** 데이터를 수집합니다. 다른 기업을 수집하려면 `collect.py` 하단의 `collect("삼성전자")`를 원하는 회사명으로 변경하세요.

### 3. 개별 모듈 테스트

각 파일은 `python 파일명.py`로 직접 실행 가능합니다 (`if __name__ == "__main__"` 블록 포함).

```bash
python dart_api.py      # API 7개 함수 테스트
python dart_report.py   # 사업보고서 파싱 테스트
python corp_code.py     # 기업 고유번호 조회 테스트
```

---

## 데이터 흐름 요약

```
DART Open API
    │
    ├── corpCode.xml (zip) ──→ corp_codes.csv (캐시)
    │
    ├── company.json      ──→ companies 테이블
    ├── list.json         ──→ disclosures 테이블
    ├── fnlttSinglAcnt    ──→ finances 테이블
    ├── empSttus.json     ──→ employees 테이블
    └── document.xml      ──→ reports 테이블
                (ZIP 파싱 → 챕터별 텍스트)
```

---

## 향후 확장 포인트

- **DB 교체:** `db.py`의 `ENGINE_URL`만 MySQL 연결 문자열로 변경하면 됩니다
- **챕터 추가:** `collect.py`에서 `get_report_text(rcept_no, sections=[...])` 파라미터로 원하는 챕터를 지정할 수 있습니다
- **다중 기업 수집:** `collect("기업명")`을 반복 호출하면 여러 기업을 순차 수집할 수 있습니다
- **RAG 파이프라인 연동:** `reports` 테이블의 텍스트를 청킹해 벡터 DB에 저장하면 LLM 기반 기업 분석이 가능합니다
