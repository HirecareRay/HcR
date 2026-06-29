# 기업 분석 보고서 RAG 파이프라인 명세서 v2

> 갱신: 2026-06-24 | 담당: 데이터팀
> 목적: 회사별 분석 보고서 생성(RAG) → `company_analyses` 적재까지 전체 흐름.
>
> **v1 → v2 변경 (조장님 피드백 반영)**
> - 🔑 **근거(evidence) 보존** — 분석섹션이 `{summary, evidence}` 구조로 (신뢰도 추적)
> - 업계현황 vs 최근동향 **역할 분리**, **데이터 없음 규칙** 강화
> - **경쟁사 데이터 실제 반영** (이전엔 설명만 있고 미사용)
> - 테이블: 분석섹션 **LONGTEXT → JSON**

---

## 0. 한 줄 요약
회사 데이터(뉴스·DART·인재상·잡플래닛·경쟁사)를 모아 → LLM이 **근거(evidence) 포함** 보고서 생성 →
`reports/{company_id}.json` → (DB) `company_analyses` 적재.

---

## 1. 파이프라인

```
[1] 수집 (팀원 완료)     뉴스 / DART / 인재상 / 잡플래닛 / 채용공고
        ↓
[2] 연결                build_company_id_map.py → company_id_map.json
        ↓
[3] RAG 생성 (본인)      build_company_report.py / build_all_reports.py
        ↓
[4] 산출               reports/{company_id}.json  (근거 포함)
        ↓
[5] 적재 (DB)          company_analyses  (with engine.begin() + 배치커밋)
```

---

## 2. 데이터 소스 & 연결률 (company_id 기준)

| 소스 | 위치 | 연결률 |
|---|---|---|
| 뉴스 | `news_preprocess/article/` | 97% |
| 잡플래닛 | `scrapy/jobplanet_review/` (616개) | 74% (영문음차 미매칭) |
| DART 재무 | `dartAPI/data/재무지표.json` | 85% |
| 인재상·사업설명 | `company-crawler/data/company-crawler.json` (인재상 109개) | 이름 매칭 |
| 경쟁사 | `similar_companies.jsonl` (규칙 기반) | company_id 직접 |

> 미매칭(영문·약칭): CJ ENM↔씨제이이앤엠 등 → 음차 alias 보강이 향후 과제.

---

## 3. RAG 생성 방식

- **배치 요약** (벡터 X): 회사별 최근 뉴스 20개 + DART + 인재상 + 잡플래닛 + 경쟁사 → 1회 LLM 호출
- 모델: `gpt-4.1-mini`, Responses API, **구조화 출력(strict)**
- **할루시네이션 방지**: "주어진 데이터만, 없으면 '해당 데이터 없음', 추론 금지"

### 프롬프트 핵심 규칙 (조장님 피드백)
- `industry_status` = 산업 구조·시장 경쟁·수요 (외부 환경)
- `recent_trends` = 최근 뉴스의 회사 관련 사건·변화·리스크 (뉴스 없으면 추정 금지)
- `financial_analysis` = DART 있을 때만 숫자 해석, 없으면 "재무 데이터 없음"
- `jobplanet_review_summary` = pros/cons 균형, 없으면 "리뷰 데이터 없음"
- `growth_potential` = 종합하되 근거 없는 낙관론 금지
- SWOT = S/W/O/T 각 2~3개 (O/T는 외부환경·뉴스에서)
- **각 섹션 evidence에 근거 데이터(뉴스제목·재무수치·리뷰문구) 인용**

---

## 4. 보고서 스키마 (★ v2 핵심 변경)

분석 섹션은 `{summary, evidence}` 구조 — **근거 추적 가능**.

```jsonc
{
  "industry_status":   { "summary": "...", "evidence": ["..."] },
  "recent_trends":     { "summary": "...", "evidence": ["..."] },
  "financial_analysis":{ "summary": "...", "evidence": ["2024 매출 8조", "ROE 6.49%"] },
  "jobplanet_review_summary": { "summary": "...", "evidence": ["..."] },
  "growth_potential":  { "summary": "...", "evidence": ["..."] },
  "swot_strengths":    ["...", "..."],   // 2~3개 (프롬프트 강제)
  "swot_weaknesses":   ["...", "..."],
  "swot_opportunities":["...", "..."],
  "swot_threats":      ["...", "..."],
  "key_points":        ["...", "..."],
  "company_id": "...", "company_name": "..."
}
```

### company_analyses 테이블 (JSON 컬럼)
```sql
CREATE TABLE company_analyses (
    analysis_id BIGINT PRIMARY KEY AUTO_INCREMENT,
    company_id VARCHAR(24) NOT NULL,
    industry_status JSON, recent_trends JSON,
    financial_analysis JSON, jobplanet_review_summary JSON, growth_potential JSON,
    swot_strengths JSON, swot_weaknesses JSON,
    swot_opportunities JSON, swot_threats JSON,
    key_points JSON,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_company (company_id)
);
```
> ⚠️ v1의 LONGTEXT → **전부 JSON** (summary+evidence 구조라서). 백엔드 모델과 컬럼명·타입 합의 필수.
> 경쟁사 분석 섹션은 없음 — 경쟁사는 `similar_companies` 테이블 + 입력으로만 사용.

---

## 5. 스크립트

| 파일 | 역할 |
|---|---|
| `build_company_id_map.py` | 소스 → company_id 매핑 |
| `build_company_report.py "회사명"` | 회사 1개 보고서 (evidence 포함) |
| `build_all_reports.py [--limit N] [--overwrite]` | 전체 배치 (재개·실패격리) |
| `extract_jobplanet_display.py "회사명"` | 잡플래닛 표시용 (최신2+긍정/부정+전체) |

```bash
python build_all_reports.py --limit 5 --overwrite   # 테스트 (새 스키마)
python build_all_reports.py                          # 전체
```

---

## 6. 조장님 피드백 처리 내역

| 피드백 | 처리 |
|---|---|
| 근거 출처 미보존 | ✅ `{summary, evidence}` 구조 |
| 업계현황↔최근동향 중복 | ✅ 프롬프트 역할 분리 |
| 데이터 없음 처리 약함 | ✅ 소스별 "없으면 명시·추론금지" |
| SWOT 개수 미제한 | 🟡 프롬프트로 2~3개 강제 (strict 모드 minItems 미지원 가능) |
| 경쟁사 미반영 | ✅ gather에서 similar_companies 읽고 프롬프트에 주입 |
| OT 비었음(이전) | ✅ S/W/O/T 4개 생성 |
| 웹검색 SWOT | 🟡 향후 — 현재 뉴스로 외부요인. 웹검색 툴은 추가 옵션 |
| LangChain | ⚠️ 단순 RAG엔 불필요 (종속성↑). raw API 유지 |

---

## 7. 다음 단계
- [ ] `--overwrite`로 새 스키마(evidence) 보고서 전체 생성
- [ ] **company_analyses 테이블 생성** (JSON 컬럼) + 백엔드 모델 합의
- [ ] **적재 스크립트** — `reports/*.json` → INSERT (`with engine.begin()` + 1000개 배치커밋)
- [ ] 음차 alias 보강 (잡플래닛/DART 영문이름 연결률↑)
- [ ] (선택) SWOT 스키마 minItems/maxItems — API 지원 확인 후
- [ ] (향후) 웹검색 툴로 외부요인 강화

---

## 8. 핵심 메모
- **evidence = 신뢰도의 핵심.** LLM이 무엇을 보고 말했는지 DB에 남아 추적 가능.
- **RAG = 배치 요약** (벡터·청킹 불필요. 청킹은 팀원이 DB news에 완료).
- **company_id가 모든 소스를 묶는 키** (company_id_map.json).
- DB 적재 시 **커넥션 누수·락 점유 주의** — `with engine.begin()` + 배치커밋.
- 표시용(JOIN) vs 생성용(LLM) 구분 — LLM은 분석 섹션만, 기업개요·직원수·인재상·채용공고·경쟁사목록은 SELECT.
