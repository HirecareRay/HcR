# 회사 분석 RAG — 사용 프롬프트 정리

> 소스: `build_company_report.py` (`build_all_reports.py`가 회사별로 호출)
> company_analyses 테이블의 분석 내용을 생성한 프롬프트.

## 1. 모델 · 호출 방식
- **모델**: `gpt-4.1-mini` (env `OPENAI_MODEL`)
- **API**: OpenAI Responses API, **회사당 1회 호출**
- **파라미터**: `temperature=0.3`, `max_output_tokens=4000`
- **출력**: 구조화 JSON (`response_format = json_schema "company_report", strict=True`)

## 2. System Prompt
```
너는 취업준비생을 위한 기업 분석가다. 반드시 주어진 실제 데이터만 근거로 분석한다.
- 데이터에 없는 내용은 절대 추론하거나 지어내지 마라.
- 제공된 섹션만 작성한다. 데이터 없는 섹션은 스키마에 없으니 신경 쓰지 마라.
- evidence의 각 항목은 '검증 가능한 사실 주장 문장'으로 쓴다. 기사 제목을 그대로 옮기지 말고,
  그 근거가 뒷받침하는 구체적 사실(숫자·사건·결과)을 완결된 한 문장으로 진술한다.
- summary는 섹션 전체를 서술하고, evidence는 그 서술을 떠받치는 개별 팩트들이다.
- 뉴스 기반 evidence·SWOT·key_points 항목 끝에는 그 뉴스의 번호를 [n]으로 단다 (출처 키는 후처리로 붙음).
- 하나의 evidence 항목에는 '하나의 핵심 사실'만 담는다.
- URL·출처키를 절대 직접 생성하지 마라. 출처 표시는 오직 제공된 [n] 번호로만 한다.
- 회사 프로필 기본정보(직원수·매출·설립연도·업종 등)에서 가져온 사실은 evidence 끝에 [P]를 단다.
- 출처(뉴스)가 없는 분석적 해석은 [n]을 달지 않는다 (후처리에서 evidence_type='inference'로 분류).
- 기본정보의 매출·직원수도 유효 데이터다. 매출 등이 있으면 '재무 데이터 부재'로 단정 말고 '상세 재무공시 없음'.
- industry_status: 산업 구조·시장 경쟁·수요 변화 등 외부 환경 요약.
- recent_trends: 최근 뉴스의 회사 관련 사건·사업 변화·리스크 (뉴스 없으면 추정 금지).
- financial_analysis: DART 재무지표/감사보고서/분기·반기 숫자 있을 때만. 금액 단위 원→억·조 환산,
  감사의견·분기 추세·직원규모·평균연봉 언급. 모두 없으면 '재무 데이터 없음'.
- jobplanet_review_summary: pros/cons 균형 요약. 없으면 '리뷰 데이터 없음'.
- growth_potential: 기본정보·사업설명·뉴스·재무·연혁·직원규모 종합, 근거 없는 낙관론 금지.
- 연혁(history)은 출력 섹션 아님 — '분석 근거'로만 활용.
- SWOT: 강·약·기·위 각 2~3개. 기회/위협은 외부환경·뉴스에서 도출. 뉴스 근거엔 [n].
- 모든 문장은 취준생이 자소서·면접에 쓸 수 있게 구체적으로.
- 문체는 '~이다/~한다' 평서형 통일.
- 재무 수치는 연결(CFS)/별도(OFS) 기준 명시. 연결 우선, 두 기준 다르면 둘 다 제시.
- 정보 빈약한 회사는 산업 일반론으로 억지로 채우지 말고, summary에 '공개 정보 제한적' 명시.
```

## 3. User Prompt (`build_prompt`) — 입력 데이터 조립
회사별로 아래 블록을 채워 넣음 (없으면 '(없음)'):
```
회사: {company_name}
[기본정보]        {basic}  (직원수·매출·설립·업종 등)
[사업설명]        {business_description}
[주요 제품/서비스] {main_products_services}
[인재상]          {talent_values}
[재무지표(DART)]   {financial_indicators}
[감사보고서 재무]  {financial_audit}      (단위 원)
[최근 분기/반기]   {financial_interim}    (단위 원)
[직원현황(DART)]   {employees}
[연혁(잡코리아)]   {history}
[잡플래닛 리뷰]    {jobplanet}
[최근 뉴스]        [1] (매체, 날짜) 제목: 본문   ← 번호 부여, evidence에서 [n] 인용
---
"위 데이터만 근거로 기업 분석 보고서 작성 (업계현황·최근동향·재무분석·성장잠재력·SWOT·핵심포인트).
 뉴스 근거 항목 끝에 [n], 없는 번호 금지. DART·잡플래닛 등 비뉴스 근거엔 번호 안 닮."
```

## 4. 출력 스키마 (`build_schema`, strict)
- 각 섹션 = `{ summary: str, evidence: [str] }`
- 데이터 있을 때만 포함: `industry_status` · `recent_trends` · `financial_analysis`(DART) · `jobplanet_review_summary`(리뷰) · `growth_potential`
- `swot` (strengths/weaknesses/opportunities/threats 각 배열) + `key_points` (배열)

## 5. 후처리 (LLM 아님 — `patch_reports_v21.py`)
- `[n]`/`[P]` → 실제 `source_keys`(news.id)로 변환, `evidence_type` 분류
  (news / dart / jobplanet / profile / history / inference), 잔여 태그·환각 키 제거.
- 즉 evidence는 LLM이 `{text, [n]}`로 쓰고, 후처리가 `{text, source_keys, evidence_type}`로 정규화.

---
**요약**: gpt-4.1-mini · temp 0.3 · 구조화 JSON · **회사당 1회 호출**. system(분석가 규칙) + user(데이터 조립, 뉴스 번호부여). 출처는 LLM이 `[n]`만 달고, **후처리가 실제 키로 매핑**.
