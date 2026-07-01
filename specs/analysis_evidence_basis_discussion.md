# 분석보고서 evidence·근거(basis) 설계 — 팀 논의용

> 회사분석 리포트(`company_analyses`)의 evidence 기준을 어떻게 잡을지 정리.
> 관련 발의: `wonder1ng`(24·25 사업요약/근거 기준/항목화), `rong9835`(근거 노출 범위 질문).

---

## 0. 현재 구조 (넷마블 샘플 기준)

각 섹션 = `{ summary, evidence: [ { text, source_keys, evidence_type } ] }`

- 섹션: `industry_status, recent_trends, financial_analysis, growth_potential, swot_*, key_points`
- `evidence_type`: `news`(뉴스 인용) / `profile`(기업 기본팩트) / `financial`(DART) / `inference`(출처 없는 AI 해석)
- `source_keys` → `source.json`의 문서(뉴스 id 등)로 매핑 = **"어디서 왔나"**

**핵심 한계**: 출처(어디서)는 있는데, **"왜 이게 핵심/이슈/강점인지"(기준)** 가 없음 → `wonder1ng` 발의 지점.

---

## 1. `wonder1ng` 제안 3가지 → 스키마 매핑

### ① 24·25년 사업요약 (기존 financial_analysis 안, 신설 X)
```json
"financial": {
  "2024": { "text": "매출 …, 영업이익 …", "basis": { "source": "DART 공시", "fy": "2024" } },
  "2025": { "text": "매출 …",            "basis": { "source": "catch 추정", "fy": "2025" } }
}
```
- DART 있으면 `공시`(확정), 없으면 catch `추정` 라벨 → **추정을 확정처럼 안 보이게**

### ② 근거 "기준"(basis) — 왜 이슈/강점인지
출처(`source_keys`)는 *어디서*, **`basis`는 *왜 핵심으로 뽑혔나***. **LLM 아니라 후처리 룰로 계산**(날짜·개수·출처종류는 데이터에 있어 정확·일관).

```json
{
  "text": "신작 '솔:인챈트'가 출시 직후 양대 마켓 매출 1위",
  "source_keys": ["넷마블-000197_0"],
  "evidence_type": "news",
  "basis": {
    "selected_by": "recent_volume",   // 왜 핵심: 최근 가장 많이 다뤄진 토픽
    "related_count": 8,               // ← "n개월 중 유사 문서 최다"
    "recency_days": 7,
    "source_rank": "news"
  }
}
```

`selected_by` 종류 (= `wonder1ng` 예시 그대로):

| 기준 | 의미 | 계산(후처리) |
|---|---|---|
| `recent_volume` | 최근 N개월 **유사 문서 최다** | 관련 뉴스 건수 |
| `recency` | 최근 발생 | 뉴스 date |
| `official_filing` | **DART 공시** = 확정 | dart 소스 |
| `ir_source` | **IR 발표** 자료 | source_type=ir |
| `company_profile` | 기업 기본 팩트 | companies(매출 등) |

→ `source_rank` 우선순위: **공시 > IR > 뉴스 > catch(추정)**

### ③ 문장 항목별 다듬기
- evidence/key_point는 이미 항목 리스트 ✅
- `summary`도 프롬프트에 *"줄글 말고 항목별(•)"* 지시

---

## 2. "유사 문서 최다"를 어떻게 셀까 (basis 정확도의 핵심)

`related_count`를 세려면 "같은 토픽 뉴스"를 묶어야 함. 방식:

| 방식 | 어떻게 | 장점 | 단점 |
|---|---|---|---|
| **A. 키워드/제목 매칭** | 핵심어(게임명 등) 추출 → 포함 뉴스 카운트 | 단순·비용0·설명쉬움 | 표현차 놓침 |
| B. 임베딩 유사도 | 벡터 코사인 클러스터 | 정확 | 모델·비용·인프라 |
| C. LLM 토픽 태깅 | 뉴스별 토픽 라벨 | 의미기반 | LLM호출↑ |

**추천: A로 시작 + 기존 인용 활용**
- LLM이 이미 key_point에 **관련 source_keys 여러 개를 묶음** → 그걸 씨앗으로
- 씨앗 뉴스의 핵심어가 든 **다른 최근 뉴스를 키워드로 확장** → `related_count`
- 새 클러스터링 안 짜도 됨, 비용 0

---

## 3. 추가 결정 포인트 (3개 외에 같이 정할 것)

1. **추정 vs 공시 라벨** ⭐ — catch=추정, DART=공시. 충돌 시 **DART 우선** 규칙 + 라벨 표기
   - (실제 사례: CJ ENM 매출 catch "2조7,837억" vs DART "2조7,838억" 차이)
2. **basis 노출 범위** (`rong9835` 질문) — `related_count` 같은 수치를 **UI 노출 vs 내부 정확도용**? → 추천: 출처 링크 노출, 수치는 내부, 사용자엔 "최근 집중 보도" 문구
3. **빈 섹션 정책** — 뉴스/DART 없는 회사 → 빈칸 / "데이터 없음" / 숨김 (일관 규칙)
4. **SWOT 구조화** — 현재 SWOT은 문자열 배열이라 basis 못 붙임. "왜 강점인지"를 적용하려면 `{text, source_keys, basis}` 구조로 (작업량↑)
5. **유사판정 윈도우·임계** — n=몇 개월? related_count 몇 건 이상이면 "핵심 이슈"?
6. **분석 갱신 시점** — 뉴스 계속 쌓임. 언제 재생성(수동/주기/공고갱신)

---

## 4. 논의 우선순위 (제안)

| 순위 | 항목 | 이유 |
|---|---|---|
| 1 | 유사판정 방식(A) + 윈도우/임계 | basis가 흔들리지 않으려면 먼저 |
| 1 | 추정/공시 라벨 + DART 우선 | financial 신뢰도 |
| 2 | basis 노출 범위 / SWOT 구조 | UI·작업량 |
| 3 | 빈 섹션 정책 / 갱신 시점 | 운영 |

→ **`wonder1ng` 3개("무엇을" 보강)는 좋고, 1·2(그 보강이 정확하려면 먼저 정할 "기준")부터 합의하자**는 흐름.

---

## 5. 재생성 범위 (참고)

- 전체 1091 중 **완전 빈(stub) = 353개**. 그중 채울 재료 있는 건 **206개**(catch 191 주력), 나머지 147은 매출·업종 숫자뿐(서술 없어 보류 권장).
- **이미 채워진 jobkorea 784개는 재생성 X.** basis는 LLM 0으로 **기존 전체에 후처리**로 부착 가능.
- 즉 "전부 재생성"이 아니라 **빈 206개 채움 + 전체 basis 후처리**.
