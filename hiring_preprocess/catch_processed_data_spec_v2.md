# HCR 캐치 전처리 데이터 명세서 v2

작성일: 2026-06-19  
대상 원본: `catch_rawdata.tsv`  
스키마 버전: HCR 채용공고 정규화 v2

## 1. 산출물 개요

| 파일 | 레코드 수 | 용도 | DB 적재 |
|---|---:|---|---|
| `catch_full_normalized_v2.jsonl` | 437 | 정규화된 채용공고 | 대상 |
| `catch_full_normalized_v2_preprocess_audit.jsonl` | 437 | OCR, LLM, 삭제 및 검수 로그 | 선택 |

각 파일은 JSONL 형식이다. 한 줄이 하나의 독립된 JSON 객체이며 전체 파일을 하나의 JSON 배열로 감싸지 않는다.

레코드 수가 원본과 같아도 처리 성공을 의미하지는 않는다. DB 적재 전 `raw_meta.llm_error`, `raw_meta.review_required`, `jobs`를 반드시 확인한다.

### 1.1 현재 산출물 품질 요약

| 항목 | 건수 | 판정 |
|---|---:|---|
| 전체 공고 | 437 | 정상 파싱 |
| LLM 정상 처리 | 437 | 오류 없음 |
| LLM 오류 | 0 | 정상 |
| 자동 적재 권장 | 427 | `review_required=false` |
| 수동 검수 필요 | 10 | 적재 보류 권장 |
| 빈 `jobs[]` | 3 | 재처리 필요 |
| 추출된 전체 직무 | 743 | 공고당 복수 직무 포함 |
| OCR 사용 공고 | 437 | 캐치 전체 이미지형 공고 |
| 저품질 OCR 감지 | 8 | 감사 로그 확인 필요 |
| 회사명 누락 | 0 | 정상 |
| 공고명 누락 | 0 | 정상 |
| 고용형태 누락 | 0 | 정상 |
| 마감일 누락 | 0 | 정상 |
| 지원 URL 누락 | 0 | 정상 |
| 원공고 URL 중복 | 0 | 정상 |

현재 산출물은 전체적으로 DB 적재 가능한 수준이다. 다만 437건을 일괄 적재하기보다 자동 통과 427건과 수동 검수 10건을 분리하는 것을 권장한다.

## 2. 파일 연결 기준

정규화 파일과 감사 로그는 다음 필드를 조합해 연결한다.

```text
source_url
source_file + source_row
```

권장 고유 식별자는 `source_site + source_url`이다. 동일 URL이 중복 수집될 가능성이 있으면 수집 시점 또는 내부 공고 ID를 별도로 추가한다.

## 3. 공통 값 규칙

| 표현 | 의미 |
|---|---|
| `""` | 원문에서 확인하지 못한 문자열 |
| `[]` | 확인된 항목이 없거나 추출하지 못한 목록 |
| `null` | 숫자 변환이 불가능하거나 미정인 값 |
| `true`, `false` | 처리 여부 또는 검수 여부 |

- 문자열은 UTF-8로 저장한다.
- 날짜는 가능한 경우 `YYYY-MM-DD` 형식을 사용한다.
- URL은 `http://` 또는 `https://` 형식을 사용한다.
- OCR 또는 LLM이 추론할 수 없는 값은 임의 생성하지 않고 비워 둔다.

## 4. 정규화 파일

파일: `catch_full_normalized_v2.jsonl`

### 4.1 최상위 필드

| 필드 | 타입 | 필수 | 설명 |
|---|---|---:|---|
| `company_name` | string | Y | 회사명 |
| `posting_title` | string | Y | 채용공고 제목 |
| `source_site` | string | Y | 출처 플랫폼. 캐치는 `catch` |
| `source_url` | string | Y | 원공고 URL |
| `common` | object | Y | 공고 전체에 공통으로 적용되는 조건 |
| `jobs` | array<object> | Y | 모집 직무 목록 |
| `process` | array<string> | Y | 채용 전형 순서 |
| `work_conditions` | object | Y | 고용 및 근무 조건 |
| `raw_meta` | object | Y | 원본 및 전처리 실행 메타데이터 |

### 4.2 `common`

| 필드 | 타입 | 설명 |
|---|---|---|
| `education` | string | 공고 전체 공통 학력 조건 |
| `major` | string | 공고 전체 공통 전공 조건 |
| `preferred` | array<string> | 모든 직무에 적용되는 공통 우대사항 |
| `documents` | array<string> | 모든 직무에 적용되는 공통 제출서류 |

직무에만 적용되는 조건은 `common`이 아니라 해당 `jobs[]`에 저장한다.

### 4.3 `jobs[]`

| 필드 | 타입 | 설명 |
|---|---|---|
| `job_name` | string | 모집 직무 또는 모집분야명 |
| `headcount` | string | 원문 모집인원 표현 |
| `headcount_value` | integer \| null | 숫자로 변환한 모집인원 |
| `education` | string | 해당 직무 학력 조건 |
| `major` | string | 해당 직무 전공 조건 |
| `locations` | array<string> | 해당 직무 근무지 |
| `responsibilities` | array<string> | 신입과 경력에 공통인 담당업무 |
| `preferred_common` | array<string> | 해당 직무의 신입/경력 공통 우대사항 |
| `tracks` | object | 신입/경력별 세부 조건 |

`headcount`의 `"0명"`, `"00명"`, `"○명"`은 원문 보존값이다. 실제 0명으로 해석하지 않으며 `headcount_value`는 `null`이 될 수 있다.

### 4.4 `jobs[].tracks`

`newcomer`는 신입, `experienced`는 경력 트랙이다. 두 객체의 구조는 동일하다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `requirements` | array<string> | 필수 자격요건 |
| `preferred` | array<string> | 해당 트랙에만 적용되는 우대사항 |
| `responsibilities` | array<string> | 해당 트랙에만 적용되는 담당업무 |
| `documents` | array<string> | 해당 트랙에만 적용되는 제출서류 |

원공고가 신입/경력을 구분하지 않으면 두 트랙이 비어 있거나 동일하게 추출될 수 있다. 동일 내용은 감사 로그의 경고와 함께 검수한다.

### 4.5 `work_conditions`

| 필드 | 타입 | 설명 |
|---|---|---|
| `employment_type` | string | 정규직, 계약직, 인턴 등 고용형태 |
| `work_type` | string | 근무시간 또는 근무형태 |
| `salary` | string | 급여 또는 연봉 조건 |
| `benefits` | array<string> | 복리후생 |
| `deadline` | string | 지원 마감일, 권장 형식 `YYYY-MM-DD` |
| `recruit_url` | string | 실제 지원 URL. 없으면 `source_url` 사용 |

이메일 주소나 `"홈페이지 참고"` 같은 문구는 `recruit_url`로 사용하지 않는다.

### 4.6 `raw_meta`

| 필드 | 타입 | 설명 |
|---|---|---|
| `source_file` | string | 원본 파일 경로 |
| `source_row` | integer | 원본 파일 행 번호. 헤더를 고려해 일반적으로 2부터 시작 |
| `source_url` | string | 원공고 URL |
| `ocr_used` | boolean | 유효한 OCR 텍스트 사용 여부 |
| `llm_used` | boolean | LLM 정규화 성공 여부 |
| `llm_error` | string | LLM 오류 메시지. 정상은 `""` |
| `ocr_low_quality` | boolean | OCR 품질 저하 감지 여부 |
| `text_source` | string | LLM 입력 텍스트 선택 방식 |
| `input_mode` | string | 공고 입력 처리 경로 |
| `image_count` | integer | 발견한 이미지 URL 수 |
| `review_required` | boolean | 수동 검수 필요 여부 |

#### `text_source` 값

| 값 | 의미 |
|---|---|
| `image_ocr` | 상세 텍스트와 이미지 OCR을 사용 |
| `normal` | 일반 텍스트를 정상 사용 |
| `low_quality_but_rich` | 품질은 낮지만 정보량이 많은 OCR을 포함 |
| `ocr_excluded` | 저품질 OCR을 LLM 입력에서 제외 |
| `ocr_failed_text_fallback` | OCR 실패 후 기존 상세 텍스트 사용 |
| `page_fetched` | 원공고 페이지 텍스트로 보완 |
| `fallback` | 사용할 텍스트가 부족해 대체 처리 |

#### `input_mode` 값

| 값 | 의미 |
|---|---|
| `image_ocr_llm` | 이미지 OCR 후 LLM 정규화 |
| `html_llm` | HTML 텍스트 정제 후 LLM 정규화 |
| `text_llm` | 일반 텍스트를 LLM으로 정규화 |

### 4.7 DB 적재 전 권장 검증

다음 조건을 만족하는 레코드를 우선 적재 대상으로 본다.

```text
raw_meta.llm_used == true
raw_meta.llm_error == ""
jobs.length > 0
raw_meta.review_required == false
```

`review_required=true`인 레코드는 자동 삭제하지 않고 감사 로그와 함께 수동 검수 큐로 보낸다.

### 4.8 현재 캐치 적재 권장안

#### 자동 적재

다음 조건을 만족하는 427건을 우선 적재한다.

```text
raw_meta.llm_used == true
raw_meta.llm_error == ""
raw_meta.review_required == false
jobs.length > 0
```

#### 적재 보류

`review_required=true`인 10건은 원문 또는 감사 로그 확인 전까지 적재를 보류한다.

| 원본 행 | 회사 | 공고 | 직무 수 | 주요 사유 |
|---:|---|---|---:|---|
| 6 | 한국교육학술정보원 | 2026년 제5차 직원(계약직 및 청년인턴) 채용 | 8 | 저품질 OCR 제외, 직무 수 확인 필요 |
| 97 | 한국국제협력단 | 2026년 일반직 및 공무직 채용 | 5 | 직무 정보 반복 가능성 |
| 134 | 강원랜드 | 2026년 1차 신입직 채용 | 0 | 빈 `jobs[]`, 재처리 필수 |
| 157 | 엠지데이터시스템 | MG데이터시스템 계약직 채용 | 0 | 저품질 OCR 및 fallback, 재처리 필수 |
| 207 | 엠지데이터시스템 | MG데이터시스템 일반직(정규직) 채용 | 5 | 저품질 OCR 및 fallback |
| 211 | 대한장애인체육회 | 2026년 제2차 채용 | 5 | 저품질 OCR 제외 |
| 213 | 레디포스트 | Senior Software Engineer(Back-End 개발자) | 1 | 저품질 OCR 및 fallback |
| 221 | 그랜드코리아레저 | 2026년 GKL 신입사원 채용 | 12 | 저품질 OCR 제외, 직무 분리 확인 필요 |
| 352 | 아이센스 | 2026년 상반기 신입/경력 대규모 채용 | 0 | 빈 `jobs[]`, 재처리 필수 |
| 411 | 티시스아이티 | 2026년 부문별 경력사원 채용 | 5 | 저품질 OCR을 정보량 기준으로 사용 |

재처리 최우선 대상은 `jobs[]`가 비어 있는 강원랜드, 엠지데이터시스템 계약직, 아이센스 3건이다.

### 4.9 빈 `jobs[]` 발생 원인

빈 `jobs[]`는 LLM API 호출 실패가 아니라, OCR 결과만으로 직무 단위를 안전하게 구분할 수 없어 보수적으로 비워 둔 결과다.

현재 프롬프트는 실제 모집표 또는 본문에서 직무 단위를 확인할 수 없을 때 임의로 직무를 만들지 않도록 규정한다. 잘못된 직무, 근무지, 자격요건을 합쳐 저장하는 것보다 빈 배열과 검수 표시를 남기는 정책이다.

| 원본 행 | 회사 | 발생 원인 | 권장 재처리 |
|---:|---|---|---|
| 134 | 강원랜드 | 9개 모집분야가 복잡한 이미지 표로 구성됨. OCR 텍스트는 존재하지만 행과 열의 관계가 사라져 직무별 조건 연결이 어려움 | 원공고 HTML 표 파싱 또는 비전 모델 사용 |
| 157 | 엠지데이터시스템 | 이미지 OCR 품질이 낮고 기존 상세 텍스트도 부족해 `fallback` 처리됨 | 고해상도 이미지 재수집 또는 원공고 페이지 텍스트 보완 |
| 352 | 아이센스 | 다수 직무가 포함된 이미지 표의 OCR 품질이 낮아 OCR 입력을 제외함. 직무 생성 근거가 부족해짐 | 고해상도 OCR, HTML 표 파싱 또는 비전 모델 사용 |

단일 직무 fallback을 자동 생성하는 방식은 공고 제목과 검색 사이트 카테고리를 실제 모집 직무로 오인할 수 있어 기본 정책으로 사용하지 않는다.

## 5. 전처리 감사 로그

파일: `catch_full_normalized_v2_preprocess_audit.jsonl`

### 5.1 최상위 필드

| 필드 | 타입 | 설명 |
|---|---|---|
| `company_name` | string | 회사명 |
| `posting_title` | string | 공고 제목 |
| `source_site` | string | 출처 플랫폼 |
| `source_url` | string | 원공고 URL |
| `source_file` | string | 원본 파일 경로 |
| `source_row` | integer | 원본 행 번호 |
| `review_required` | boolean | 수동 검수 필요 여부 |
| `llm_input_hash` | string | LLM 입력 텍스트 SHA-256 |
| `preprocess_log` | object | 삭제, 불확실성, 경고 및 원문 스냅샷 |

### 5.2 `preprocess_log`

| 필드 | 타입 | 설명 |
|---|---|---|
| `dropped_fields` | array<object> | 스키마에 넣지 못한 원문 정보 |
| `low_confidence` | array<object> | 신뢰도가 낮은 추출 또는 분류 |
| `parse_warnings` | array<string> | OCR, 날짜, 직무 분리 등의 경고 |
| `original_text_snapshot` | object | OCR 및 LLM 입력 일부 |

### 5.3 `dropped_fields[]`

| 필드 | 타입 | 설명 |
|---|---|---|
| `field` | string | 누락 또는 제외된 필드명 |
| `original_value` | string | 원문 값 |
| `reason` | string | 제외 이유 |

### 5.4 `low_confidence[]`

| 필드 | 타입 | 설명 |
|---|---|---|
| `field` | string | 불확실한 대상 필드 |
| `original_text` | string | 판단 근거가 된 원문 |
| `issue` | string | 불확실한 이유 |
| `confidence` | number | 신뢰도. 일반적으로 0과 1 사이 |

### 5.5 `original_text_snapshot`

| 필드 | 타입 | 설명 |
|---|---|---|
| `raw_ocr_excerpt` | string | OCR 원문 앞부분, 최대 약 1,200자 |
| `final_text_excerpt` | string | LLM 입력 앞부분, 최대 약 1,200자 |
| `llm_input_hash` | string | 전체 LLM 입력의 SHA-256 |

스냅샷은 디버깅용 일부 텍스트이며 전체 원문의 영구 보관을 대체하지 않는다.

## 6. 고용형태 처리 정책

프리랜서를 포함한 모든 고용형태를 정규화 파일에 저장한다. 서비스에서 특정 고용형태를 숨기거나 우선순위를 낮추는 정책은 전처리 결과를 삭제하지 않고 DB 조회 조건 또는 서비스 설정으로 적용한다.

## 7. 권장 DB 구조

초기 적재는 정규화 레코드 전체를 JSONB로 보존하는 방식을 권장한다.

```text
job_postings
- id
- source_site
- source_url
- company_name
- posting_title
- deadline
- normalized_data JSONB
- review_required
- created_at
- updated_at
```

직무 검색과 매칭 성능이 필요하면 `jobs[]`를 별도 `job_positions` 테이블로 분리한다.

감사 로그는 별도 테이블 또는 객체 스토리지에 저장하고 `source_url`이나 내부 공고 ID로 연결한다.

## 8. 운영 체크리스트

1. JSONL 파싱 오류가 없는지 확인한다.
2. 원본 수와 정규화, 감사, 제외 수의 합을 확인한다.
3. `llm_error`가 있는 레코드는 재처리한다.
4. 빈 `jobs[]`는 검수 대상으로 분류한다.
5. `review_required=true`는 감사 로그와 함께 확인한다.
6. `source_site + source_url` 중복을 확인한다.
7. DB 적재 후 원본 파일과 캐시는 삭제하지 않는다.

## 9. 품질 해석 주의사항

감사 로그 437건 중 다수에 `parse_warnings`가 존재하지만 모든 경고가 데이터 오류를 의미하지는 않는다.

주요 안내성 경고:

- 모집인원 미기재 또는 `"0명"`, `"00명"` 등 비공개 표현
- 급여 정보 미기재
- `"본사 홈페이지 지원"` 같은 비 URL 값을 `source_url`로 교체
- 원문 시간대 제거 후 날짜만 저장

우선 검수가 필요한 실질적 경고:

- 빈 `jobs[]`
- 저품질 OCR
- `fallback` 또는 `ocr_excluded`
- 서로 다른 직무에 동일한 업무와 조건 반복
- 직무 수가 원공고의 모집분야와 일치하지 않을 가능성

경고 개수만으로 레코드를 폐기하지 않고 `review_required`와 경고 내용을 함께 판단한다.

## 10. 최종 적재 결론

```text
전체 437건
├── 자동 적재 권장: 427건
└── 수동 검수 보류: 10건
    ├── 재처리 필수: 3건
    └── 원문 비교 검수: 7건
```

현 버전은 전체 LLM 처리 성공률 100%, 주요 최상위 필드 충족률 100%이며 운영용 초기 데이터로 사용할 수 있다. 다만 OCR 기반 자동 정규화 결과이므로 감사 로그와 원본 추적 정보를 함께 보존해야 한다.

## 11. 팀 공유 및 저장소 업로드 권장안

전체 JSONL은 기계 처리에는 적합하지만 한 줄에 하나의 긴 JSON 객체가 저장되므로 GitHub 화면에서 사람이 직접 검토하기는 어렵다.

권장 공유 구성:

| 자료 | 공유 위치 | 목적 |
|---|---|---|
| 데이터 명세서 | Git 저장소 | 필드와 품질 정책 설명 |
| 정상 3건 + 검수 2건 샘플 JSONL | Git 저장소 | 코드 실행 및 파싱 예제 |
| 샘플 README 또는 미리보기 문서 | Git 저장소 | 사람이 읽는 결과 설명 |
| 전체 437건 JSONL | DB, 공유 드라이브 또는 객체 스토리지 | 실제 적재 및 분석 |
| OCR/LLM 캐시 | 로컬 또는 별도 스토리지 | 재처리 비용 절감 |

Git 저장소에는 다음 세 파일만 우선 공유하는 것을 권장한다.

```text
catch_processed_data_spec_v2.md
data/processed/catch_sample5_normalized_v2.jsonl
data/processed/catch_sample5_preprocess_audit.jsonl
data/processed/catch_sample5_README.md
```

전체 데이터 파일을 Git에 올려야 한다면 원본과 개인정보 포함 여부, 파일 크기, 갱신 빈도를 먼저 확인한다. 결과가 반복 생성되는 대용량 산출물이라면 Git 이력에 계속 누적되므로 일반 코드 저장소보다는 DB나 파일 스토리지가 더 적합하다.

## 12. 사용 기술 및 라이브러리

| 라이브러리 | 역할 |
|---|---|
| Python 3 | 전처리 파이프라인 실행 |
| `pandas` | CSV, TSV, JSON, JSONL 입력 로드 |
| `requests` | 이미지, 원공고 페이지, OpenAI Responses API 요청 |
| `Pillow` | 다운로드 이미지 검증 및 처리 |
| `tqdm` | 대량 공고 처리 진행률 표시 |
| `easyocr` | 한국어와 영어 이미지 텍스트 추출 |

OpenAI Python SDK는 사용하지 않는다. `.env`의 `OPENAI_API_KEY`를 읽고 `requests`를 통해 OpenAI Responses API를 직접 호출한다.

그 밖에 `json`, `pathlib`, `hashlib`, `re`, `argparse` 등 Python 표준 라이브러리를 사용한다.
