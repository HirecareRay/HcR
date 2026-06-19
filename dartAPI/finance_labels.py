"""
finance_labels.py
감사보고서 손익·재무 라벨 → 표준 재무필드 매핑 (정규화 + 동의어 데이터).

감사보고서는 회사·회계법인마다 양식이 달라 같은 계정도 표기가 제각각이다.
  예) "매출액" · "Ⅰ.매출" · "매 출 액(주석14)" · "영업수익" · "I. 영업수익"
표본 분석 결과 의미(semantic) 라벨은 필드당 3~5개로 수렴하고,
변형의 대부분은 포맷 노이즈(머리번호 · 단어 내부 공백 · 주석 suffix · 손익 양방향)였다.

따라서 라벨을 정규화해 노이즈를 제거한 뒤 표준필드에 매핑한다.
새 변형은 수집실패 로그로 확인해 동의어 테이블을 보강하는 피드백 루프로 운영한다.
"""
import re

# ── 표준 재무필드 (figures dict의 키 순서 — 출력·스키마와 동일하게 유지) ──────────
FINANCE_FIELDS: tuple[str, ...] = (
    # IS
    "revenue",
    "operating_income",
    "net_income",
    # BS
    "total_assets",
    "total_liabilities",
    "total_equity",
    # SCE (자본총계와 정의상 동일 — get_audit_text에서 채움, 스캔 대상 아님)
    "ending_capital",
    # CF
    "operating_cash_flow",
    "investing_cash_flow",
    "financing_cash_flow",
)

# 끝의 주석참조·손익부호 괄호: (주석15) (주10) (주석2,9) (손실) (이익) 등만 제거 대상.
# 의미 있는 괄호(예: 단위 표기)는 보존한다.
_NOTE_INNER = re.compile(r"^주석?[:：]?[\d,.및\s]*$|^손실$|^이익$")
_HANGUL = re.compile(r"[가-힣]")


def normalize_label(raw: str) -> str:
    """
    재무제표 행 라벨에서 포맷 노이즈를 제거해 의미 라벨만 남긴다.

      "Ⅰ.매 출 액(주석:14)" → "매출액"
      "I. 영업수익"          → "영업수익"
      "Ⅴ. 영업이익(손실)"    → "영업이익"
      "당기순손실(이익)"      → "당기순손실"

    절차: 공백 제거 → 첫 한글 이전 머리번호 제거 → 끝의 주석/손익 괄호 반복 제거.
    """
    s = raw.replace("\xa0", "").replace(" ", "").strip()

    # 머리번호(로마숫자·아라비아·영문 enumerator 등) 제거: 첫 한글 이전을 모두 버린다.
    m = _HANGUL.search(s)
    if m:
        s = s[m.start():]

    # 끝의 주석참조·손익부호 괄호를 반복적으로 제거 ("(손실)(주석11)" 같은 중첩 대응)
    while True:
        mm = re.search(r"[\(（]([^()（）]*)[\)）]$", s)
        if mm and _NOTE_INNER.match(mm.group(1)):
            s = s[: mm.start()]
        else:
            break

    return s


# ── 1차 매칭: 정규화 라벨 정확 일치 (고정밀, 우선순위 순) ──────────────────────────
# 손익 양방향·주석 suffix는 normalize_label이 이미 제거하므로 기본형만 등록한다.
FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "revenue":             ("매출액", "매출", "영업수익", "수익"),
    "operating_income":    ("영업이익", "영업손실", "영업손익"),
    "net_income":          ("당기순이익", "당기순손실", "당기순손익",
                            "분기순이익", "분기순손실", "반기순이익", "반기순손실"),
    "total_assets":        ("자산총계", "자산합계"),
    "total_liabilities":   ("부채총계", "부채합계"),
    "total_equity":        ("자본총계", "자본합계"),
    "operating_cash_flow": ("영업활동현금흐름", "영업활동으로인한현금흐름"),
    "investing_cash_flow": ("투자활동현금흐름", "투자활동으로인한현금흐름"),
    "financing_cash_flow": ("재무활동현금흐름", "재무활동으로인한현금흐름"),
}

# ── 2차 매칭: 부분 일치 fallback (저정밀, 1차에서 못 잡은 필드만) ──────────────────
# 정규화 후에도 추가 텍스트가 붙은 라벨(예: "매출액(단위:천원)")을 흡수한다.
# 같은 키워드를 포함하지만 의미가 다른 계정은 exclude로 걸러낸다.
FIELD_CONTAINS: dict[str, dict[str, tuple[str, ...]]] = {
    "revenue": {
        "keywords": ("매출", "영업수익"),
        "exclude": (
            "매출원가", "매출총이익", "매출채권", "매출에누리", "매출할인",
            "제품매출", "상품매출", "기타매출", "국내매출", "해외매출",
        ),
    },
    "total_assets": {
        "keywords": ("자산총계", "자산합계"),
        "exclude": (),
    },
}
