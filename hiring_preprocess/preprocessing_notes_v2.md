# 채용공고 전처리 메모 v2

작성일: 2026-06-17

## v2 변경 목적

v1 구조는 `jobs[].newcomer`와 `jobs[].experienced`만 있어 직무 공통 우대사항, 신입 전용 우대사항, 경력 전용 우대사항이 섞일 수 있었다. 실제 채용공고는 공고 전체, 직무, 신입/경력 트랙의 3단계 레이어를 가지므로 v2에서는 이 구조를 반영한다.

## 실행

```bash
/usr/bin/python3 /Users/monapark/HcR/hiring_preprocess/clean_all_jobs.py
```

OpenAI API를 사용하려면 `.env`에 API key를 둔다.

```bash
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1-mini
```

OCR만 테스트하려면 LLM을 끌 수 있다.

```bash
USE_LLM=0 /usr/bin/python3 /Users/monapark/HcR/hiring_preprocess/clean_all_jobs.py
```

## 입력과 출력

입력:

```text
hiring_preprocess/data/raw/*.csv
hiring_preprocess/data/raw/*.tsv
hiring_preprocess/data/raw/*.json
hiring_preprocess/data/raw/*.jsonl
```

출력:

```text
hiring_preprocess/data/processed/jobs_normalized_v2.jsonl
hiring_preprocess/data/cache/ocr_cache.json
hiring_preprocess/data/cache/llm_cache_v2.json
```

## v2 JSON 구조 핵심

```text
공고 레벨
  common
  jobs[]
    직무 공통 정보
    preferred_common
    tracks
      newcomer
      experienced
  preprocess_log
```

우대사항 분류 기준:

- `common.preferred`: 공고 전체에 적용되는 공통 우대사항
- `jobs[].preferred_common`: 해당 직무의 신입/경력 모두에 적용되는 우대사항
- `jobs[].tracks.newcomer.preferred`: 신입 전용 우대사항
- `jobs[].tracks.experienced.preferred`: 경력 전용 우대사항

불분명한 경우 더 공통적인 상위 레벨에 배치한다.

## preprocess_log

LLM/OCR 전처리에서 정보가 사라지거나 애매하게 분류되는 문제를 추적하기 위해 `preprocess_log`를 추가했다.

```json
{
  "preprocess_log": {
    "dropped_fields": [],
    "low_confidence": [],
    "parse_warnings": [],
    "original_text_snapshot": {
      "raw_ocr_excerpt": "",
      "final_text_excerpt": "",
      "llm_input_hash": ""
    }
  }
}
```

활용 목적:

- `dropped_fields`: 원문에는 있었지만 스키마에 넣지 못한 값 추적
- `low_confidence`: 직무/트랙/우대사항 분류가 애매한 값 검수
- `parse_warnings`: 날짜, 위치, 인원, OCR 오타 등 파싱 이슈 확인
- `llm_input_hash`: 원문 추적과 LLM 캐시 검증

## raw_meta

`raw_meta`는 출처 추적용으로 유지한다.

```json
{
  "raw_meta": {
    "source_file": "",
    "source_row": 0,
    "source_url": "",
    "ocr_used": true,
    "llm_used": true,
    "llm_error": ""
  }
}
```

`source_row`는 TSV/CSV 원본 행을 다시 찾기 위한 값이다.


## v2.1 프롬프트 보강

초기 v2 결과에서 여러 모집분야가 하나의 `jobs[]` 항목으로 합쳐지는 문제가 확인되었다. 예를 들어 원자력발전소 계측제어, 원자력발전소 MMIS 설비 정비, 화력발전소 계측제어, 기술연구소 하드웨어 등이 하나의 job으로 합쳐지고 모든 근무지가 섞이는 문제가 있었다.

이를 막기 위해 LLM 프롬프트에 다음 규칙을 추가했다.

```text
모집분야/직무명이 여러 개면 반드시 jobs[]를 여러 개로 분리하세요.
서로 다른 모집분야의 근무지, 전공, 자격요건, 담당업무를 하나의 job에 합치지 마세요.
표에서 같은 행 또는 같은 모집분야 블록에 있는 정보만 같은 job에 넣으세요.
```

추가 보존 규칙:

- `0명`, `00명`은 미기재가 아니라 원문 표현이므로 `headcount`에 그대로 보존한다.
- `deadline`은 timezone을 추정하지 않고 `YYYY-MM-DD` 문자열로 보존한다.
- 이메일 주소는 `recruit_url`에 넣지 않는다.
- 이메일/전화번호처럼 스키마에 전용 필드가 없는 값은 `preprocess_log.parse_warnings`에 기록한다.
- 같은 우대사항을 `preferred_common`과 `tracks.*.preferred`에 중복 기록하지 않는다.

프롬프트 변경으로 기존 v2 LLM 캐시를 재사용하지 않도록 `SCHEMA_VERSION`을 `hcr_job_schema_v2_1`로 변경했다.


## v2.2 필드 분류 보강

v2.1 결과에서 일부 자격요건이 `responsibilities`에 들어가고, 특정 직무 우대사항이 `common.preferred`로 과하게 올라가는 문제가 확인되었다. 이를 줄이기 위해 프롬프트에 다음 규칙을 추가했다.

```text
자격요건, 필수조건, 경력조건, 학력요건, 자격증 보유 조건은 responsibilities에 넣지 말고 requirements 또는 education에 넣으세요.
responsibilities에는 실제 수행 업무만 넣으세요.
preferred에는 우대사항만 넣으세요.
특정 직무명 주변에만 등장하는 우대사항은 common.preferred가 아니라 해당 job의 preferred_common 또는 track.preferred에 넣으세요.
common.preferred에는 공고 전체 모든 직무에 적용된다고 명확한 우대사항만 넣으세요.
```

프롬프트 변경으로 `SCHEMA_VERSION`을 `hcr_job_schema_v2_2`로 올렸다.


## v2.3 education/common.preferred 보강

v2.2 결과에서 `education`에 산업기사 같은 자격 조건이 들어가고, 특정 직무 우대사항이 `common.preferred`로 과하게 올라가는 문제가 일부 남았다.

추가한 규칙:

```text
education에는 학력만 넣으세요. 예: 고졸, 전문학사, 학사, 석사, 박사, 무관.
산업기사, 기사, 면허, 자격증, 어학점수, 경력연수는 education이 아니라 requirements에 넣으세요.
common.preferred에는 공고 전체 모든 직무에 적용된다고 명확한 우대사항만 넣으세요.
특정 직무, 연구소, 발전소 유형, 트랙 주변에 등장한 우대사항은 common.preferred에 넣지 마세요.
```

이 변경으로 `SCHEMA_VERSION`을 `hcr_job_schema_v2_3`으로 올렸다. 이후에는 샘플 몇 개를 더 확인하되, 과도한 프롬프트 튜닝보다는 품질 리포트와 수동 검수 대상으로 넘기는 것을 권장한다.


## v2.4 규칙 기반 후처리 보강

LLM 출력 이후 DB 적재와 품질 추적을 위해 가벼운 후처리를 추가했다.

- `headcount` 원문은 보존하고, 숫자 쿼리용 `headcount_value`를 추가한다.
  - `0명`, `00명`, 빈 값은 `None`으로 둔다.
  - `3명`처럼 숫자가 명확하면 `3`으로 저장한다.
- `tracks.newcomer`와 `tracks.experienced`의 requirements/preferred/responsibilities가 동일하면 `parse_warnings`에 경고를 남긴다.
- LLM 실패 또는 빈 deadline의 경우 원문에서 날짜 후보를 찾아 fallback deadline을 채운다. 기간 표현은 마지막 날짜를 사용한다.
- `source_site`가 비어 있으면 source URL 또는 source file에서 `catch`, `jobkorea`, `incruit`을 추론한다.
- `work_conditions.recruit_url`이 비어 있으면 `source_url`로 fallback한다.
- OCR/LLM 캐시는 5건마다 저장하고, `finally`에서 한 번 더 저장해 중단 시 손실을 줄인다.
- 긴 JSON Schema는 `job_schema_v2.json`으로 분리했다.

이 변경으로 `SCHEMA_VERSION`을 `hcr_job_schema_v2_4`로 올렸다.
