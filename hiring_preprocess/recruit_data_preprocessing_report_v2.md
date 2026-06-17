# 채용공고 데이터 전처리 리포트 v2

작성일: 2026-06-17

## 변경 요약

v2에서는 OCR 이후 LLM 정규화 결과를 실제 채용공고 구조에 맞게 재설계했다. 기존 v1은 `newcomer`와 `experienced`만 분리되어 우대사항과 담당업무가 섞일 수 있었다. v2는 공고 공통, 직무 공통, 신입/경력 트랙을 분리한다.

## 주요 결정사항

1. 출력 파일은 기존 결과와 섞이지 않도록 `jobs_normalized_v2.jsonl`로 분리한다.
2. LLM 캐시는 `llm_cache_v2.json`으로 분리한다.
3. 캐시 키에는 `SCHEMA_VERSION = hcr_job_schema_v2`를 포함한다.
4. `common` 블록을 추가해 공고 전체 공통 학력, 전공, 우대사항, 제출서류를 보관한다.
5. `jobs[].preferred_common`을 추가해 직무 단위 공통 우대사항을 보관한다.
6. `jobs[].tracks.newcomer`와 `jobs[].tracks.experienced`를 두어 신입/경력 전용 요건을 분리한다.
7. `preprocess_log`를 추가해 누락, 낮은 신뢰도, 파싱 경고를 추적한다.
8. `raw_meta.source_row`를 추가해 원본 TSV/CSV 행 역추적이 가능하도록 한다.

## v2 스키마 방향

```json
{
  "company_name": "",
  "posting_title": "",
  "source_site": "",
  "source_url": "",
  "common": {
    "education": "",
    "major": "",
    "preferred": [],
    "documents": []
  },
  "jobs": [
    {
      "job_name": "",
      "headcount": "",
      "education": "",
      "major": "",
      "locations": [],
      "responsibilities": [],
      "preferred_common": [],
      "tracks": {
        "newcomer": {
          "requirements": [],
          "preferred": [],
          "responsibilities": [],
          "documents": []
        },
        "experienced": {
          "requirements": [],
          "preferred": [],
          "responsibilities": [],
          "documents": []
        }
      }
    }
  ],
  "process": [],
  "work_conditions": {
    "employment_type": "",
    "work_type": "",
    "salary": "",
    "benefits": [],
    "deadline": "",
    "recruit_url": ""
  },
  "raw_meta": {},
  "preprocess_log": {}
}
```

## LLM 프롬프트 보완

LLM에는 다음 원칙을 명시한다.

```text
우대사항을 다음 3가지 레벨로 분류하세요.
- common.preferred: 공고 전체에 적용되는 공통 우대사항
- jobs[].preferred_common: 해당 직무 신입/경력 모두에 해당하는 우대사항
- jobs[].tracks.newcomer/experienced.preferred: 해당 트랙에만 해당하는 우대사항

불분명한 경우 상위 레벨, 즉 더 공통적인 쪽에 배치하세요.
```

또한 전처리 품질 추적을 위해 다음을 기록하게 한다.

```text
- dropped_fields: 원문에는 있었지만 스키마에 넣지 못했거나 버린 값
- low_confidence: 분류가 애매하거나 신뢰도가 낮은 판단
- parse_warnings: 날짜, 위치, 인원, 제출서류 등 파싱 이슈
- original_text_snapshot.llm_input_hash: 전체 LLM 입력 추적 해시
```

## 기대 효과

- fit scoring 시 `preferred_common + tracks.<type>.preferred`를 조합해 계산 가능하다.
- UI에서 직무 카드별 공통 요건과 신입/경력 요건을 분리해 표시할 수 있다.
- OCR/LLM 품질 이슈를 `preprocess_log`로 모아 수동 검수와 프롬프트 개선에 활용할 수 있다.
- 원본 행 추적이 쉬워져 데이터 디버깅 비용이 줄어든다.


## v2.1 품질 점검 및 보완

샘플 실행 결과, LLM이 여러 직무를 하나의 `jobs[]` 항목으로 합치는 문제가 확인되었다. 특히 근무지와 담당업무가 여러 모집분야에서 섞여 하나의 직무 카드에 들어가는 문제가 있었다.

확인된 문제:

- 복수 직무가 하나의 job으로 병합됨
- `locations`에 전체 공고의 모든 지역이 섞임
- `headcount`의 `0명`, `00명` 표현이 누락됨
- 이메일 접수 주소가 `work_conditions.recruit_url`에 들어감
- `deadline`이 timezone 포함 ISO 문자열로 변환됨
- 동일 우대사항이 `preferred_common`과 track별 `preferred`에 중복됨

보완 내용:

1. 모집분야/직무명이 여러 개면 `jobs[]`를 반드시 여러 개로 분리하도록 프롬프트를 강화했다.
2. 서로 다른 모집분야의 근무지, 전공, 자격요건, 담당업무를 하나의 job에 합치지 말도록 명시했다.
3. 표에서 같은 행 또는 같은 모집분야 블록에 있는 정보만 같은 job에 넣도록 명시했다.
4. `0명`, `00명`은 `headcount`에 그대로 보존하도록 했다.
5. `deadline`은 `YYYY-MM-DD` 문자열로 보존하도록 했다.
6. 이메일은 `recruit_url`에 넣지 않고 `parse_warnings`에 기록하도록 했다.
7. 동일 우대사항의 중복 기록을 피하도록 했다.

이 변경은 프롬프트/스키마 운영 변경이므로 `SCHEMA_VERSION = hcr_job_schema_v2_1`로 올렸다.


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
