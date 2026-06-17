# 채용공고 통합 전처리 메모

`clean_all_jobs.py`는 CATCH, 잡코리아, 인크루트, 사람인 채용공고 TSV를 하나의 표준 스키마로 정리하는 스크립트다.

## 실행 예시

가장 간단한 실행:

```bash
python3 clean_all_jobs.py
```

인자를 생략하면 아래 폴더에서 TSV를 자동으로 찾는다.

```text
hiring_preprocess/data/raw
hiring_preprocess/test_data
```

파일명에 `catch`, `jobkorea`, `incruit`, `saramin`이 포함되어 있으면 source를 자동 추정한다. 이미 정제된 TSV처럼 원천 입력 스키마가 아닌 파일은 자동으로 건너뛴다.

기본 실행 시 통합 파일과 출처별 개별 파일이 함께 생성된다.

```text
outputs/all_jobs_clean.tsv
outputs/catch_jobs_clean.tsv
outputs/jobkorea_jobs_clean.tsv
outputs/incruit_jobs_clean.tsv
outputs/saramin_jobs_clean.tsv
```

개별 파일 생성을 끄려면 `--no-split-by-source` 옵션을 사용한다.

직접 파일을 지정해서 실행:

```bash
python3 clean_all_jobs.py \
  --input catch:test_data/catch_data.tsv \
  --input jobkorea:test_data/jobkorea_post_20260612_024805_508660.tsv \
  --input incruit:test_data/incruit_page0002_20260615_004855.tsv \
  --output all_jobs_test.tsv
```

입력은 `SOURCE:파일경로` 형식으로 넣는다.

지원하는 `SOURCE`:

- `catch`
- `jobkorea`
- `incruit`
- `saramin`

## 주요 원칙

1. 회사명 원본은 `company_original`에 보존한다.
2. `company_clean`은 공백 정리 정도만 수행한다.
3. DART 검색용 후보명은 `company_dart_query`에 별도로 저장한다.
4. DART 매칭 성공 후에는 회사명 대신 `corp_code`를 기준키로 사용한다.
5. 채용공고 중복 제거는 `recruit_url` 기준으로 수행한다.
6. 플랫폼별 `company_info` 구조는 별도 파서로 처리하되 출력은 같은 스키마로 통일한다.
7. 금액 정보는 원문 컬럼과 억 단위 숫자 컬럼을 함께 보존한다.

## 출력 스키마

```text
source
company_original
company_clean
company_dart_query
corp_code
title
job_categories_clean
detail_text
summary_text
deadline
image_url
ceo
corp_type
company_size
industry
address
homepage
founded_date
employees
sales_raw
sales_억원
capital_raw
capital_억원
operating_profit_raw
operating_profit_억원
net_income_raw
net_income_억원
recruit_url
rag_text
```

## 회사명 예시

| 원본 | company_clean | company_dart_query |
|---|---|---|
| `㈜두아즈` | `㈜두아즈` | `두아즈` |
| `(주)엘핀` | `(주)엘핀` | `엘핀` |
| `SK(주) AX` | `SK(주) AX` | `SK(주) AX` |

`SK(주) AX`처럼 중간에 법인 표기가 들어간 회사명은 임의 제거하지 않는다.

## GitHub 업로드 추천 구성

```text
hiring_preprocess/clean_all_jobs.py
hiring_preprocess/preprocessing_notes.md
hiring_preprocess/recruit_data_preprocessing_report.md
```

`test_data/`와 `*.tsv`는 Git에 올리지 않는 것을 권장한다.
