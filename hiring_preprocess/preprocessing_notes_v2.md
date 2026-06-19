# HCR 채용공고 전처리 Notes v2

## 1. 목표

잡코리아, 캐치, 인크루트의 서로 다른 채용공고 데이터를 DB 적재 전 공통 JSON으로 정규화한다.

- CSV, TSV, JSON, JSONL 입력 지원
- `company_info`는 정규화 대상에서 제외
- 이미지형 공고는 EasyOCR(`ko`, `en`)로 텍스트 추출
- 기존 상세 텍스트와 OCR 결과를 결합
- OpenAI LLM으로 직무 단위 JSON 생성
- 결과는 JSONL로 저장

사람인 데이터는 현재 처리 대상이 아니다.

## 2. v2 구조 변경

공고 전체, 직무, 신입/경력 트랙을 구분한다.

```text
공고
├── common
└── jobs[]
    ├── responsibilities
    ├── preferred_common
    └── tracks
        ├── newcomer
        └── experienced
```

- `common`: 공고 전체에 적용되는 학력, 전공, 우대사항, 제출서류
- `jobs[]`: 모집분야 또는 직무별 정보
- `preferred_common`: 해당 직무의 신입/경력 공통 우대사항
- `tracks.*`: 신입 또는 경력에만 적용되는 요건, 우대사항, 업무, 서류

## 3. 직무 분리 기준

- 모집분야가 여러 개면 `jobs[]`를 각각 생성한다.
- 표의 다른 행이나 별도 모집 블록을 하나의 직무로 합치지 않는다.
- 근무지, 전공, 요건, 담당업무는 같은 행 또는 같은 모집 블록에 있는 정보만 연결한다.
- 원자력발전소 계측제어와 MMIS 설비 정비처럼 별도 모집분야로 제시되면 별도 직무로 처리한다.
- 분리 근거가 불명확하면 추측하지 않고 `low_confidence` 또는 `parse_warnings`에 기록한다.

## 4. OCR 처리

1. 이미지 URL을 찾는다.
2. 이미지를 `data/cache/images/`에 저장한다.
3. EasyOCR로 한국어와 영어를 추출한다.
4. OCR 결과는 URL 기준으로 캐시한다.
5. 상세 텍스트와 OCR 결과의 품질을 비교해 LLM 입력을 결정한다.

`text_source` 값:

| 값 | 의미 |
|---|---|
| `image_ocr` | 상세 텍스트와 정상 품질 OCR을 함께 사용 |
| `html_text` | 이미지 없이 HTML/일반 텍스트 사용 |
| `low_quality_but_rich` | OCR 품질은 낮지만 정보량이 많아 포함 |
| `ocr_excluded` | 저품질 OCR을 LLM 입력에서 제외 |
| `ocr_failed_text_fallback` | OCR 실패 후 기존 상세 텍스트 사용 |
| `page_fetched` | 원문 페이지 HTML로 보완 |
| `fallback` | 사용 가능한 텍스트가 빈약하여 수동 검수 필요 |

저품질 OCR도 `raw_ocr_excerpt`에 보존한다. 원본 추적을 위해 삭제하지 않는다.

## 5. 전처리 로그

`preprocess_log`는 정규화 과정에서 잃거나 추정한 정보를 추적한다.

- `dropped_fields`: 원문에는 있으나 스키마에 넣지 못한 값과 이유
- `low_confidence`: 분류가 불명확한 필드, 원문, 사유, 신뢰도
- `parse_warnings`: OCR, 날짜, 위치, 직무 분리 등의 검수 경고
- `original_text_snapshot`: OCR 및 LLM 입력 일부와 입력 해시

이 블록은 DB 적재용 JSONL에서 제외하고 별도 audit JSONL에 저장한다.
두 파일은 `source_url`, `source_file`, `source_row`, `llm_input_hash`로 연결한다.

`raw_meta`에는 원본 파일, 행 번호, 출처 URL, OCR/LLM 사용 여부와 오류를 기록한다.

## 6. 후처리 규칙

- `headcount`는 원문을 보존한다.
- `headcount_value`는 숫자로 변환하며 `0명`, `00명`, 빈 값은 `null`로 처리한다.
- LLM이 `recruit_url`을 만들지 못하면 원공고 `source_url`을 사용한다.
- 이메일 주소는 `recruit_url`로 취급하지 않는다.
- `recruit_url`이 HTTP(S) URL이 아니면 원공고 `source_url`로 교체하고 audit 경고를 남긴다.
- 마감일은 시간대를 추정하지 않고 `YYYY-MM-DD`로 저장한다.
- 신입/경력 내용이 완전히 같으면 경고를 추가한다.
- 확인할 수 없는 문자열 필드는 빈 문자열 `""`로 저장한다.
- OCR 저품질, 빈 직무, 직무 정보 반복, LLM 오류가 있으면 `review_required=true`로 표시한다.
- 직무 절반 이상에서 핵심 정보가 비어 있어도 `review_required=true`로 표시한다.
- `신입`, `경력` 같은 트랙 레이블은 `preferred_common`에서 제거한다.
- 교육생 공고의 교육비·자격증·수료증·취업지원 등은 우대사항이 아닌 `benefits`로 분류한다.

## 7. 수동 검수 우선순위

다음 조건은 우선 검수한다.

1. `text_source`가 `fallback` 또는 `page_fetched`
2. `ocr_low_quality=true`
3. `llm_used=false` 또는 `llm_error` 존재
4. 직무 분리 또는 신입/경력 분류 관련 경고
5. `jobs`가 비어 있음
6. 회사명 또는 공고명이 비어 있음

전화번호나 이메일 전용 필드 부재, 시간대 제거 등의 안내성 경고는 낮은 우선순위로 검수한다.

원본 JSONL은 수정하지 않고, 검수 결과는 별도 JSONL과 `manual_review_log.csv`에 기록하는 것을 권장한다.

## 8. 환경변수

프로젝트 또는 스크립트 폴더의 `.env`:

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1-mini
OPENAI_TEMPERATURE=0
MAX_LLM_CHARS=12000
USE_LLM=1
```

API 키는 코드와 Git 저장소에 직접 넣지 않는다.

```bash
# API 호출 없이 OCR과 캐시만 확인
python work/clean_all_jobs.py --limit 3 --no-llm

# LLM을 포함해 전체 실행
python work/clean_all_jobs.py

# 출력 파일 직접 지정
python work/clean_all_jobs.py --input input.tsv --output data/processed/custom.jsonl

# audit 파일 경로도 직접 지정
python work/clean_all_jobs.py --input input.tsv \
  --output data/processed/custom.jsonl \
  --audit-output data/processed/custom_audit.jsonl
```

`--input`을 사용하고 `--output`을 생략하면 입력 파일명 기준으로 결과가 자동 분리된다.
예: `catch_data.tsv` → `data/processed/catch_data_normalized_v2.jsonl`
audit 파일도 `catch_data_normalized_v2_preprocess_audit.jsonl`로 자동 생성된다.

## 9. 파일

- 실행 코드: `work/clean_all_jobs.py`
- OCR/HTML 도우미: `work/ocr_utils.py`
- LLM 지시문: `work/normalization_prompt_v2.txt`
- JSON Schema: `work/job_schema_v2.json`
- LLM에는 런타임 필드를 제거한 파생 스키마를 전달
- 결과: `data/processed/jobs_normalized_v2.jsonl`
- 전처리 감사 로그: `*_preprocess_audit.jsonl`
- OCR 캐시: `data/cache/ocr_cache.json`
- LLM 캐시: `data/cache/llm_cache_v2.json`
- 페이지 캐시: `data/cache/page_cache.json`

LLM 캐시 키에는 모델, 입력 텍스트, 프롬프트 파일 해시, LLM 스키마 해시가 포함된다.
