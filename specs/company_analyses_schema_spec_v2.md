# company_analyses 출력 스키마 명세서 v2

보고서 1행(=1회사)의 **JSON 구조**. MariaDB `company_analyses`에 평면 JSON 컬럼으로 저장.

- `analysis_version: "v2"`
- 관련: [파이프라인](rag_pipeline_spec_v2.md) · [출처 규약](evidence_citation_spec_v2.md) · [DB·적재](db_schema_loading_spec_v2.md)

---

## 1. 전체 구조

```jsonc
{
  "company_id": "4c6a2dc35bec6d932b68",   // 허브 키
  "company_name": "(주)CJ ENM",
  "analysis_version": "v2",
  "generated_at": "2026-06-24",

  // ── 분석 섹션: {summary, evidence[]}. 데이터 없으면 컬럼 NULL ──
  "industry_status":          { "summary": "...", "evidence": [<EvidenceItem>] },
  "recent_trends":            { "summary": "...", "evidence": [<EvidenceItem>] },
  "financial_analysis":       { "summary": "...", "evidence": [<EvidenceItem>] },
  "jobplanet_review_summary": { "summary": "...", "evidence": [<EvidenceItem>] },
  "growth_potential":         { "summary": "...", "evidence": [<EvidenceItem>] },

  // ── SWOT: 각 항목도 EvidenceItem (출처·종류 동반) ──
  "swot_strengths":     [<EvidenceItem>],
  "swot_weaknesses":    [<EvidenceItem>],
  "swot_opportunities": [<EvidenceItem>],
  "swot_threats":       [<EvidenceItem>],

  // ── 핵심 요약: EvidenceItem 배열 (v1의 문자열 배열 → 객체화) ──
  "key_points": [<EvidenceItem>],

  // ── 메타 ──
  "source_snapshot": { ... },   // §4
  "sources": [<SourceObject>]   // §5 전역 출처 배열
}
```

## 2. EvidenceItem — 근거 1개 (핵심 단위)

```jsonc
{
  "text": "유니콘 등극과 5600억원 투자 유치로 시장 신뢰를 확보했다",  // 검증가능 '사실 주장 문장' (기사 제목 ❌)
  "source_keys": ["Upstage-000050_0"],   // 인용한 뉴스 key (전역 sources의 source_key 참조). 없으면 []
  "evidence_type": "news"                // news | dart | jobplanet | profile | history | inference (6종)
}
```

- **하나의 evidence = 하나의 핵심 사실** (여러 사실 욱여넣기 금지).
- `text`에는 `[n]`·`[P]` 인용 마커가 **제거된 상태**로 저장 (후처리 완료). → [출처 규약](evidence_citation_spec_v2.md)
- 전체 source 객체를 중복 저장하지 않고 **source_keys만** → url/media는 전역 `sources`에서 단일 관리 (DRY).

## 3. summary vs evidence 역할 분리

| | 역할 |
|---|---|
| `summary` | 섹션 **전체를 서술**하는 해석 문단 (1개 문자열) |
| `evidence[]` | 그 서술을 **떠받치는 개별 검증가능 팩트** + 출처 |

## 4. source_snapshot — 이 보고서가 쓴/가용 데이터

```jsonc
{
  "news_count": 20,                 // 사용 뉴스 건수
  "jobplanet_review_count": 120,    // 잡플 리뷰 수
  "has_dart": true,                 // DART 재무(지표/감사/분기) 유무
  "has_company_profile": true,      // 회사 프로필(기본정보·사업설명·인재상) 유무
  "has_recruit_data": true          // 채용공고 보유 여부
}
```

- v1의 `employee_count` 제거(본문 직원수와 null 불일치 유발) → `has_company_profile`로 대체.

## 5. 섹션 의미·트리거

| 섹션 | 내용 | 트리거 | evidence 기본 종류(뉴스 인용 없을 때) |
|---|---|---|---|
| industry_status | 산업 구조·경쟁·수요 등 외부 환경 | 뉴스 | inference |
| recent_trends | 최근 뉴스의 회사 사건·변화·리스크 | 뉴스 | inference |
| financial_analysis | 매출·이익·자산 해석(**연결/별도 명시**) | DART | **dart** |
| jobplanet_review_summary | pros/cons 균형 요약 | 잡플 | **jobplanet** |
| growth_potential | 기본정보·사업·뉴스·재무·연혁·직원 종합 | 항상 | inference |
| swot_* | 강·약·기·위 각 2~3개 | 항상 | inference |
| key_points | 핵심 bullet | 항상 | inference |

- 재무·연혁·해외진출·직원규모는 **분석 근거**로만 활용(연혁은 출력 섹션 아님).
- 데이터 빈약 회사: 일반론으로 부풀리지 않고 summary에 '공개 정보 제한' 명시.

## 6. NULL 정책

- 데이터 없는 섹션 → 컬럼 NULL (스키마에서 제외).
- 콘텐츠 전혀 없는 회사(업종·규모뿐 ~353개) → 분석 컬럼 전부 NULL, `company_id` 행만. → **항상 1091행**.
- **버전 구분**: 생성된 보고서 `analysis_version="v2"`(738) / 빈 stub 행 `="v2_stub"`(353). 즉 `COUNT(WHERE analysis_version='v2')` = 실제 생성된 보고서 수.
- 근거 없는 주장은 만들지 않음. 만들 경우 `evidence_type: "inference"`로 명확히 분리.
