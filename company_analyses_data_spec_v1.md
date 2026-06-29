# company_analyses 데이터 명세서 v1

AI 기업 분석 보고서(RAG) 파이프라인의 **데이터 구조 · DB 스키마 · 출처(citation) 규약** 명세.

- 산출물: MariaDB `company_analyses` (회사별 AI 분석 보고서 1행)
- 허브 키: `company_id` (모든 소스를 잇는 단일 키)
- 모델: `gpt-4.1-mini` (OpenAI Responses API, structured output, `temperature=0.3`)
- 원칙: **주어진 데이터만 근거. 데이터 없는 섹션/회사는 NULL (환각 0).**

---

## 1. 데이터 소스 → company_id 매칭

| 소스 | 내용 | 매칭 방식 | 커버리지(회사) |
|---|---|---|---|
| MariaDB `news` 테이블 | 뉴스(실제 언론사 url + media + key) | `news.company` → resolver | 626/630 |
| DART 재무지표 | 비율 지표 | corp_name → resolver | 48 |
| DART 감사보고서 | 매출·이익·자산·감사의견 | 〃 | 146 |
| DART 분기/사업반기 | 분기·반기 헤드라인 재무 | 〃 | 48 |
| DART 직원현황 | 총원·평균연봉·근속 | 〃 | 49 |
| 잡플래닛 리뷰 | pros/cons 샘플 | 파일명 → resolver | 469 |
| 연혁 (잡코리아) | 회사 이정표(분석 근거용) | company_ids 직결 | 148 |
| company-crawler | 인재상·사업설명·CEO메시지 | 회사명 | 374 |
| companies_enriched | 기본정보(업종·규모·매출 등) | company_id | 1091 |

- **매칭 규칙**: exact → normalize → safe_alias (퍼지 자동매칭 금지). 끝 영문괄호 제거 정규화 포함 (예: `와커스(WACUS)` = `와커스`). "오매칭 1개 > 미매칭 10개"의 위험을 피함.
- 재무(지표∪감사∪분기) 합집합 = **190개 회사**.

## 2. 파이프라인

```
gather(회사명)                       # 소스 수집 (resolver 매칭, 뉴스는 news 테이블 벌크로드)
  → build_prompt(data)               # 뉴스에 [1]..[N] 인용번호 부여
  → call_openai(prompt, schema)      # gpt-4.1-mini, temp 0.3, structured output
  → finalize_report                  # [n] → 실제 출처(url) 부착 + 메타 + sources
  → reports/{company_id}.json
  → load_company_analyses.py         # MariaDB 적재 (upsert)
```

- 동적 스키마: 데이터 없는 섹션은 스키마에서 제외 → 해당 컬럼 NULL.
- 1091개 중 콘텐츠 있는 회사(~745)만 보고서 생성, 나머지는 company_id 행만(분석 NULL).

## 3. company_analyses 행 구조 (JSON)

```jsonc
{
  "company_id": "4c6a2dc35bec6d932b68",
  "company_name": "(주)CJ ENM",
  "analysis_version": "v1",
  "generated_at": "2026-06-24",

  // 분석 섹션 — {summary, evidence[]}. 데이터 없으면 NULL
  "industry_status":  { "summary": "...", "evidence": [<EvidenceItem>] },
  "recent_trends":    { "summary": "...", "evidence": [<EvidenceItem>] },
  "financial_analysis":{ "summary": "...", "evidence": [<EvidenceItem>] },
  "jobplanet_review_summary": { "summary": "...", "evidence": [<EvidenceItem>] },
  "growth_potential": { "summary": "...", "evidence": [<EvidenceItem>] },

  // SWOT — 각 항목도 출처 동반
  "swot_strengths":     [<EvidenceItem>],
  "swot_weaknesses":    [<EvidenceItem>],
  "swot_opportunities": [<EvidenceItem>],
  "swot_threats":       [<EvidenceItem>],

  "key_points": ["...", "..."],          // 핵심 요약 bullet (string[])

  "source_snapshot": {                   // 메타: 무슨 소스를 얼마나 썼나
    "news_count": 20, "jobplanet_review_count": 120,
    "has_dart": true, "employee_count": 3105
  },
  "sources": [<NewsSource>]              // 사용한 뉴스 레코드 목록(검증용)
}
```

### EvidenceItem — 근거 1개 + 출처
```jsonc
{
  "text": "티빙 개인정보 유출로 주가 하락",     // 근거 서술 ([n] 인용은 제거됨)
  "sources": [<NewsSource>]                  // 인용한 뉴스 (없으면 [])
}
```
- DART·잡플래닛 근거는 뉴스가 아니라 `sources: []` (데이터 자체가 출처).
- 뉴스 근거만 url 부착.

### NewsSource — 출처 객체 (검증가능한 레코드 참조)
```jsonc
{
  "source_id": 3,                 // 프롬프트 내 인용번호
  "source_type": "news",
  "media": "조선비즈",            // 실제 언론사
  "title": "기사 제목",
  "url": "https://biz.chosun.com/...",   // 실제 언론사 url (네이버 집계 아님)
  "raw_key": "CJ_ENM-000347_0",   // news 테이블 id (역추적 키)
  "date": "2026-06-16"
}
```

## 4. 출처(citation) 규약 — 번호 인용 방식

RAG source-attribution 표준 패턴 (LangChain `RetrievalQAWithSourcesChain`, LlamaIndex `CitationQueryEngine` 등과 동일 결).

```
프롬프트: 뉴스에 [1]..[N] 번호 부여
LLM:     evidence·SWOT 끝에 [n] 인용 (예: "구글플레이 1위 [3]")
후처리:   [n] → 실제 url/media/raw_key 로 결정적 매핑
```

- **LLM은 번호만 출력** (긴 url/key 직접 안 씀) → url 환각 0.
- 범위 밖 번호([99])는 버림. 인용 없는 일반 사실은 `sources:[]` (억지 부착 안 함).
- DART/잡플 출처 키 부착은 향후 확장 가능 (`source_type:"dart"`, dart.fss.or.kr 링크 via rcept_no).

## 5. DB 스키마

```
companies (허브, company_id PK)
   ├─ company_analyses.company_id   (1:1, 분석 보고서)
   └─ news.company_id               (1:N, 뉴스 — 연결은 앱 단계 선택)
```

- 전 테이블 **collation = utf8mb4_unicode_ci** 로 통일 (조인 깨짐 방지).
- `company_analyses`: 분석 섹션·SWOT·sources 등은 **JSON 컬럼**, `company_id` UNIQUE (upsert).
- `companies`: company_id, company_name, industry, company_size, company_type, employee_count, revenue, founded, ceo, website_url, address, main_business.

### 적재 스크립트
- `load_companies.py` — companies_enriched → companies (1091행)
- `load_company_analyses.py` — reports/*.json → company_analyses. 보고서 없는 회사는 NULL 행으로 채워 **항상 1091행**.

## 6. 커버리지 요약 (1091개 기준)

| 컬럼 | 채워진 회사 |
|---|---|
| industry_status (뉴스) | ~620 |
| financial_analysis (DART 5종) | 190 |
| jobplanet_review_summary | 469 |
| 연혁 활용 (분석 근거) | 148 |
| 분석 없음(기본정보만) | ~346 → 전 섹션 NULL |

## 7. 향후 (TODO)
- 경쟁사 비교: 임베딩 유사도 선정(≥1) + 출처 달린 팩트 비교 (점수 아님). 보고서 고정필드 아닌 **동적 기능**.
- DART/잡플 출처 키 부착 (source_type 확장).
- 평가: RAGAS(faithfulness) + LLM-as-judge(Claude).
- news 테이블에 company_id 컬럼 (앱 조인용).
