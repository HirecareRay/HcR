# HCR 잡코리아 전처리 데이터 명세서 v2

작성일: 2026-06-19  
대상 원본: `jobkorea_posts/jobkorea_post_*.tsv`  
스키마 버전: HCR 채용공고 정규화 v2

## 1. 데이터 개요

잡코리아 원본 24개 TSV 파일을 OCR과 LLM으로 공통 JSON 구조로 정규화했다.

| 항목 | 건수 |
|---|---:|
| 원본 파일 | 24개 |
| 원본 공고 | 1,199건 |
| 정규화 공고 | 1,199건 |
| LLM 정상 처리 | 1,199건 |
| LLM 오류 | 0건 |
| 추출된 직무 | 1,530개 |
| 수동 검수 필요 | 191건 |
| 빈 `jobs[]` | 35건 |
| 프리랜서 포함 공고 | 373건 |
| 고유 `source_url` | 1,166개 |
| URL 중복 추가 행 | 33건 |

모든 원본 공고가 정규화 파일에 포함됐다. 프리랜서 공고도 제외하지 않는다.

## 2. 산출물

원본 TSV 한 파일마다 다음 두 파일이 생성된다.

```text
<원본파일명>_normalized_v2.jsonl
<원본파일명>_normalized_v2_preprocess_audit.jsonl
```

| 파일 | 용도 | DB 적재 |
|---|---|---|
| `*_normalized_v2.jsonl` | 정규화된 채용공고 | 대상 |
| `*_preprocess_audit.jsonl` | OCR, 삭제, 경고 및 검수 로그 | 선택 |

과거 실행에서 생성된 `*_excluded.jsonl`은 이전 프리랜서 제외 정책의 흔적이다. 현재 결과에는 사용하지 않으며 신규 실행에서는 생성하지 않는다.

JSONL은 한 줄에 하나의 JSON 객체를 저장한다. 전체 파일을 JSON 배열로 감싸지 않는다.

## 3. 파일 연결 및 식별자

정규화 데이터와 감사 로그는 다음 값으로 연결한다.

```text
source_file + source_row
llm_input_hash
```

일반적인 공고 식별에는 `source_site + source_url`을 사용할 수 있다. 다만 게임잡 계열 공고 33건이 같은 대표 URL을 공유하므로 `source_url`만으로 중복 제거하면 안 된다.

권장 식별 우선순위:

1. 플랫폼 고유 공고 ID
2. `source_file + source_row`
3. `source_site + source_url + posting_title + company_name`

## 4. 공통 값 규칙

| 표현 | 의미 |
|---|---|
| `""` | 원문에서 확인하지 못한 문자열 |
| `[]` | 확인된 항목이 없거나 추출하지 못한 목록 |
| `null` | 숫자 변환이 불가능하거나 비공개인 값 |
| `true`, `false` | 처리 및 검수 상태 |

- 문자 인코딩은 UTF-8이다.
- 날짜는 확인 가능한 경우 `YYYY-MM-DD`로 저장한다.
- 확인할 수 없는 값은 임의로 추측하지 않는다.
- 빈 값은 정보가 존재하지 않는다는 뜻이 아니라 미수집 또는 확인 불가일 수 있다.

## 5. 정규화 데이터 구조

### 5.1 최상위 필드

| 필드 | 타입 | 필수 | 설명 |
|---|---|---:|---|
| `company_name` | string | Y | 회사명 |
| `posting_title` | string | Y | 공고 제목 |
| `source_site` | string | Y | `jobkorea` |
| `source_url` | string | Y | 원공고 URL |
| `common` | object | Y | 공고 전체 공통 조건 |
| `jobs` | array<object> | Y | 모집 직무 목록 |
| `process` | array<string> | Y | 채용 전형 순서 |
| `work_conditions` | object | Y | 고용 및 근무 조건 |
| `raw_meta` | object | Y | 원본 및 처리 메타데이터 |

### 5.2 `common`

| 필드 | 타입 | 설명 |
|---|---|---|
| `education` | string | 공고 전체 공통 학력 |
| `major` | string | 공고 전체 공통 전공 |
| `preferred` | array<string> | 모든 직무에 적용되는 우대사항 |
| `documents` | array<string> | 모든 직무에 적용되는 제출서류 |

일부 직무에만 적용되는 우대사항은 `jobs[].preferred_common`에 저장한다.

### 5.3 `jobs[]`

| 필드 | 타입 | 설명 |
|---|---|---|
| `job_name` | string | 모집 직무 또는 모집분야 |
| `headcount` | string | 원문 모집인원 |
| `headcount_value` | integer \| null | 숫자로 변환한 모집인원 |
| `education` | string | 직무별 학력 |
| `major` | string | 직무별 전공 |
| `locations` | array<string> | 직무별 근무지 |
| `responsibilities` | array<string> | 신입·경력 공통 담당업무 |
| `preferred_common` | array<string> | 해당 직무 공통 우대사항 |
| `tracks` | object | 신입·경력별 세부 조건 |

`"0명"`, `"00명"`, `"○명"`은 실제 0명이 아니라 비공개 또는 미정 표현일 수 있다. 이 경우 `headcount_value`는 `null`이다.

### 5.4 `jobs[].tracks`

`newcomer`와 `experienced`는 같은 구조를 사용한다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `requirements` | array<string> | 필수 자격요건 |
| `preferred` | array<string> | 해당 트랙 전용 우대사항 |
| `responsibilities` | array<string> | 해당 트랙 전용 담당업무 |
| `documents` | array<string> | 해당 트랙 전용 제출서류 |

경력 전용 공고에서는 `newcomer` 객체를 유지하되 내부 배열을 비운다. 신입 전용 공고는 반대로 `experienced` 내부 배열을 비운다.

### 5.5 `work_conditions`

| 필드 | 타입 | 설명 |
|---|---|---|
| `employment_type` | string | 정규직, 계약직, 프리랜서, 인턴 등 |
| `work_type` | string | 근무시간 또는 근무형태 |
| `salary` | string | 급여 또는 연봉 |
| `benefits` | array<string> | 복리후생 |
| `deadline` | string | 지원 마감일 |
| `recruit_url` | string | 지원 URL. 없으면 원공고 URL |

프리랜서 공고도 다른 고용형태와 동일하게 저장한다. 서비스 노출 여부는 DB 조회 또는 UI 정책에서 결정한다.

### 5.6 `raw_meta`

| 필드 | 타입 | 설명 |
|---|---|---|
| `source_file` | string | 원본 TSV 경로 |
| `source_row` | integer | 원본 행 번호 |
| `source_url` | string | 원공고 URL |
| `ocr_used` | boolean | 유효한 OCR 텍스트 사용 여부 |
| `llm_used` | boolean | LLM 정규화 성공 여부 |
| `llm_error` | string | LLM 오류. 정상은 `""` |
| `ocr_low_quality` | boolean | 저품질 OCR 감지 여부 |
| `text_source` | string | LLM 입력 텍스트 선택 방식 |
| `input_mode` | string | 입력 처리 경로 |
| `image_count` | integer | 발견한 이미지 URL 수 |
| `review_required` | boolean | 수동 검수 필요 여부 |

#### `input_mode`

| 값 | 의미 |
|---|---|
| `text_llm` | 일반 텍스트를 LLM으로 정규화 |
| `html_llm` | HTML 텍스트 정제 후 LLM 정규화 |
| `image_ocr_llm` | 이미지 OCR 후 LLM 정규화 |

#### `text_source`

| 값 | 의미 |
|---|---|
| `html_text` | 원본 HTML 또는 상세 텍스트 사용 |
| `image_ocr` | 이미지 OCR 텍스트 사용 |
| `ocr_failed_text_fallback` | OCR 실패 후 기존 텍스트 사용 |
| `ocr_excluded` | 저품질 OCR 제외 |
| `low_quality_but_rich` | 저품질이지만 정보량이 많아 사용 |
| `fallback` | 사용 가능한 본문이 부족함 |
| `page_fetched` | 원공고 페이지 텍스트로 보완 |

## 6. 감사 로그 구조

### 6.1 최상위 필드

| 필드 | 타입 | 설명 |
|---|---|---|
| `company_name` | string | 회사명 |
| `posting_title` | string | 공고 제목 |
| `source_site` | string | 출처 플랫폼 |
| `source_url` | string | 원공고 URL |
| `source_file` | string | 원본 파일 |
| `source_row` | integer | 원본 행 번호 |
| `review_required` | boolean | 검수 필요 여부 |
| `llm_input_hash` | string | LLM 입력 SHA-256 |
| `preprocess_log` | object | 전처리 감사 정보 |

### 6.2 `preprocess_log`

| 필드 | 타입 | 설명 |
|---|---|---|
| `dropped_fields` | array<object> | 스키마에 넣지 못한 값과 이유 |
| `low_confidence` | array<object> | 신뢰도가 낮은 추출 결과 |
| `parse_warnings` | array<string> | OCR, 분류, 날짜 및 직무 경고 |
| `original_text_snapshot` | object | OCR 및 LLM 입력 일부 |

`original_text_snapshot`은 디버깅용 일부 텍스트이며 전체 원문을 대체하지 않는다.

## 7. 품질 현황

### 7.1 적재 분류

| 분류 | 건수 | 권장 처리 |
|---|---:|---|
| 자동 통과 | 1,008 | 우선 적재 가능 |
| 검수 필요 | 191 | 감사 로그와 함께 확인 |
| 빈 `jobs[]` | 35 | 재처리 우선 |

자동 통과 기준:

```text
raw_meta.llm_used == true
raw_meta.llm_error == ""
raw_meta.review_required == false
jobs.length > 0
```

### 7.2 빈 `jobs[]`

빈 직무는 LLM 오류가 아니라 원문에서 직무 단위를 안전하게 확인하지 못한 결과다.

- 전체 35건
- 마지막 게임잡 계열 원본 파일에 28건 집중
- 이미지 표 구조 손실, 부족한 본문, 대표 URL 사용 등이 원인

직무를 임의 생성하지 않고 `jobs=[]`, `review_required=true`로 보존한다.

권장 재처리:

1. 원공고 HTML 구조 파싱
2. 고해상도 이미지 또는 비전 모델 사용
3. 게임잡 고유 상세 URL과 공고 ID 수집

### 7.3 마감일

| 항목 | 건수 |
|---|---:|
| `deadline` 존재 | 127 |
| `deadline` 빈 값 | 1,072 |

빈 마감일 1,072건은 모두 OCR 실패를 의미하지 않는다.

| 입력 형태 | 건수 | 해석 |
|---|---:|---|
| 텍스트형 | 813 | 원본 텍스트에서 명확한 마감 날짜 확인 불가 |
| 이미지형 | 257 | 현재 OCR 텍스트에서 마감 날짜 확인 불가 |
| HTML형 | 2 | 명확한 마감 날짜 확인 불가 |

이미지형 257건 중:

- OCR 텍스트 사용: 208건
- OCR 실패 또는 미사용: 49건
- 저품질 OCR 감지: 6건

이미지형 전체를 육안 검수하지 않았으므로 `deadline=""`은 “마감일 없음”보다 “미수집 또는 확인 불가”로 해석한다.

잡코리아 목록 또는 상세 페이지에서 마감일을 별도 필드로 수집하는 것이 가장 정확하다.

### 7.4 고용형태

- 고용형태 빈 값: 53건
- 이 중 원본에 관련 표현이 있으나 놓친 것으로 추정되는 건: 약 36건

향후 원본 `detail`과 `etc_info`에서 규칙 기반 fallback 추출을 추가할 수 있다.

### 7.5 URL 중복

URL 중복 추가 행 33건은 주로 게임잡 계열 공고가 같은 대표 URL을 사용한 결과다.

```text
https://www.gamejob.co.kr/List_GI/GIB_Read.asp
```

따라서 `source_url`만으로 중복 제거하지 않는다.

## 8. 사용 기술

| 기술 | 역할 |
|---|---|
| Python 3 | 전처리 실행 |
| `pandas` | TSV 및 JSONL 처리 |
| `requests` | 이미지, 페이지, OpenAI API 요청 |
| `Pillow` | 이미지 검증 |
| `tqdm` | 진행률 표시 |
| `easyocr` | 한국어·영어 OCR |
| OpenAI Responses API | 직무 단위 JSON 정규화 |

OpenAI Python SDK 대신 `requests`로 Responses API를 직접 호출한다.

## 9. DB 적재 권장안

초기에는 전체 정규화 객체를 JSONB로 보존하는 방식을 권장한다.

```text
job_postings
- id
- source_site
- source_url
- source_file
- source_row
- company_name
- posting_title
- employment_type
- deadline
- normalized_data JSONB
- review_required
- created_at
- updated_at
```

검색과 추천에 필요한 경우 `jobs[]`를 별도 직무 테이블로 분리한다.

```text
job_positions
- id
- posting_id
- job_name
- education
- major
- headcount_value
- position_data JSONB
```

감사 로그는 별도 테이블 또는 객체 스토리지에 보존한다.

## 10. 팀 공유 권장안

Git 저장소:

- 데이터 명세서
- 정상·검수 샘플 JSONL
- 샘플 미리보기 README
- 전처리 코드, 스키마, 프롬프트

별도 스토리지 또는 DB:

- 전체 1,199건 JSONL
- 전체 감사 로그
- OCR 및 LLM 캐시

전체 JSONL은 기계 처리에 적합하지만 GitHub에서 사람이 직접 읽기는 어렵다. 팀 검토에는 5~10건 샘플과 사람이 읽는 미리보기 문서를 함께 제공한다.

## 11. 운영 체크리스트

1. 24개 정규화 파일의 합계가 1,199건인지 확인한다.
2. 감사 로그도 1,199건인지 확인한다.
3. `llm_error`가 0건인지 확인한다.
4. `review_required=true` 191건을 검수 큐로 보낸다.
5. 빈 `jobs[]` 35건을 우선 재처리한다.
6. 마감일 빈 값은 “미수집/확인 불가”로 취급한다.
7. `source_url`만으로 중복 제거하지 않는다.
8. 과거 `_excluded.jsonl`은 사용하지 않는다.
9. 원본 TSV와 캐시를 삭제하지 않는다.

## 12. 최종 결론

```text
잡코리아 원본 1,199건
├── 자동 적재 권장: 1,008건
└── 수동 검수: 191건
    └── 빈 jobs[] 재처리 우선: 35건
```

전체 LLM 처리는 성공했으며 JSON 구조도 유효하다. 현재 데이터는 초기 DB 적재와 분석에 사용할 수 있지만, 검수 대상과 마감일 미수집 상태를 구분해 운영해야 한다.

