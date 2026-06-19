"""
normalize.py
DART 원본 응답(직원현황·재무지표)을 RAG 친화적 k,v 구조로 정규화한다.

원본 문제:
  - 직원현황(empSttus): 사업부문(fo_bbm)×성별(sexdstn)로 행이 쪼개진다.
    삼성전자 등 대기업은 DX·DS 같은 부문별 + 성별합계 행으로 한 연도가 6행이
    되고, 부문 행은 급여가 "-"이며 전사 급여는 '성별합계' 행에만 담긴다.
    또한 개정전 인원·단시간근로자·비고 등 대부분 "-"인 빈 컬럼이 다수 섞인다.
  - 재무지표(fnlttSinglIndx): 지표 1개가 1행이라 회사·연도 메타데이터가
    지표 수만큼 중복된다(인스웨이브 2개년만으로 132행).

정규화 방향(감사보고서 형식처럼):
  회사·연도 단위로 1레코드를 만들고, 의미 없는 컬럼을 제거하며,
  콤마가 섞인 문자열 수치를 숫자(int/float)로 변환한다. 값이 없으면("-"/빈값)
  None으로 통일한다(감사보고서의 null 표기와 일관).
"""


# ── 숫자 변환 유틸 ─────────────────────────────────────────────────────────────

def _to_int(raw) -> int | None:
    """'91,806' → 91806, '-'/''/None → None."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if s in ("", "-"):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _to_float(raw) -> float | None:
    """'13.2' → 13.2, '-'/''/None → None."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ── 직원현황 정규화 ────────────────────────────────────────────────────────────

def _employee_metrics(row: dict) -> dict:
    """
    성별 단위 직원 지표만 추려 숫자로 변환한다.
    원본의 reform_bfe_emp_co_*(개정전 인원), *_abacpt_labrr_co(단시간근로자),
    rm(비고), corp_cls 등 거의 항상 "-"/메타인 컬럼은 버린다.
    """
    return {
        "head_count":   _to_int(row.get("sm")),                 # 직원수 합계
        "regular":      _to_int(row.get("rgllbr_co")),          # 정규직
        "contract":     _to_int(row.get("cnttk_co")),           # 계약직
        "avg_tenure":   _to_float(row.get("avrg_cnwk_sdytrn")), # 평균 근속연수
        "avg_salary":   _to_int(row.get("jan_salary_am")),      # 1인 평균 급여
        "total_salary": _to_int(row.get("fyer_salary_totamt")), # 연간 급여 총액
    }


# 표준 enum 값만 영어로 통일한다. 회사별 가변 부문명(DX/DS 등)·DART 지표명은
# 번역이 불가/손실이라 원천 한글 그대로 둔다.
_SEX_MAP:      dict[str, str] = {"남": "male", "여": "female"}
_TOTAL_LABELS: set[str]       = {"전사", "성별합계", "합계"}


def _norm_division(fo_bbm: str | None) -> str:
    """
    사업부문 키 정규화 — 전사·성별합계 등 전체 집계 표기는 'total'로 통일.
    부문 미구분("-"/빈값)도 사실상 전사이므로 'total'로 본다.
    """
    label = (fo_bbm or "").strip()
    if label in _TOTAL_LABELS or label in ("", "-"):
        return "total"
    return label


def _norm_sex(sexdstn: str | None) -> str:
    """성별 키 정규화 — 남→male, 여→female, 그 외(전체 등)는 원천 유지."""
    label = (sexdstn or "전체").strip()
    return _SEX_MAP.get(label, label)


def normalize_employees(rows: list[dict]) -> list[dict]:
    """
    직원현황 원본 행들을 (회사·연도) 단위 레코드로 묶는다.

    구조: 사업부문(division) → 성별(sex) → 지표.
      대기업은 부문이 여러 개(DX/DS/…) + 전체 집계('total')로 나뉘고,
      소기업은 단일 부문이 곧 'total'이라 둘 다 동일한 중첩으로 표현된다.
      성별·전체집계 같은 표준 enum은 영어(male/female/total)로 통일하고,
      회사별 가변 부문명(DX/DS 등)은 원천 그대로 둔다.
    """
    grouped: dict[tuple, dict] = {}
    order: list[tuple] = []

    for row in rows:
        key = (row.get("corp_code"), row.get("bsns_year"))
        if key not in grouped:
            grouped[key] = {
                "corp_name": row.get("corp_name"),
                "corp_code": row.get("corp_code"),
                "bsns_year": row.get("bsns_year"),
                "stlm_dt":   row.get("stlm_dt"),
                "rcept_no":  row.get("rcept_no"),
                "divisions": {},
            }
            order.append(key)

        # 표준 enum(성별·전체집계)은 영어로, 회사별 부문명(DX/DS)은 원천 그대로.
        division = _norm_division(row.get("fo_bbm"))
        sex      = _norm_sex(row.get("sexdstn"))
        grouped[key]["divisions"].setdefault(division, {})[sex] = _employee_metrics(row)

    return [grouped[k] for k in order]


# ── 재무지표 정규화 ────────────────────────────────────────────────────────────

def normalize_indicators(rows: list[dict]) -> list[dict]:
    """
    재무지표 원본 행들을 (회사·연도) 단위 레코드로 묶는다.

    구조: 지표분류(idx_cl_nm: 수익성/안정성/성장성/활동성지표) → 지표명(idx_nm) → 값.
      reprt_code·idx_cl_code·idx_code 등 코드 메타는 버리고 의미값만 남긴다.
    """
    grouped: dict[tuple, dict] = {}
    order: list[tuple] = []

    for row in rows:
        key = (row.get("corp_code"), row.get("bsns_year"))
        if key not in grouped:
            grouped[key] = {
                "corp_name":  row.get("corp_name"),
                "corp_code":  row.get("corp_code"),
                "bsns_year":  row.get("bsns_year"),
                "stlm_dt":    row.get("stlm_dt"),
                "stock_code": row.get("stock_code"),
                "indicators": {},
            }
            order.append(key)

        # 분류(idx_cl_nm)·지표명(idx_nm)은 원천 한글(수익성지표/ROE 등) 그대로 키로 쓴다.
        category = row.get("idx_cl_nm") or "기타"
        name     = row.get("idx_nm")
        if name:
            grouped[key]["indicators"].setdefault(category, {})[name] = _to_float(row.get("idx_val"))

    return [grouped[k] for k in order]


# ── 재무제표 정규화 ────────────────────────────────────────────────────────────

# 계정 행의 금액 필드(원본 → 정규화 키). 값 없으면("-"/빈값) 드롭한다.
_FINANCE_AMOUNT_FIELDS: dict[str, str] = {
    "thstrm_amount":     "current",       # 당기
    "thstrm_add_amount": "current_cumul", # 당기 누적(분기·반기 보고서)
    "frmtrm_amount":     "prior",         # 전기
    "frmtrm_q_amount":   "prior_q",       # 전기 동분기(분기·반기)
    "frmtrm_add_amount": "prior_cumul",   # 전기 누적(분기·반기)
    "bfefrmtrm_amount":  "prior2",        # 전전기(연간 보고서)
}

# 보고서 단위로 고정인 기수명(period label) — 메타로 끌어올린다.
_FINANCE_PERIOD_FIELDS: dict[str, str] = {
    "thstrm_nm":    "current",
    "frmtrm_nm":    "prior",
    "frmtrm_q_nm":  "prior_q",
    "bfefrmtrm_nm": "prior2",
}


def _finance_amounts(row: dict) -> dict:
    """계정 한 행에서 금액 필드만 추려 숫자로 변환한다(빈 값 드롭)."""
    out: dict = {}
    for src, dst in _FINANCE_AMOUNT_FIELDS.items():
        val = _to_int(row.get(src))
        if val is not None:
            out[dst] = val
    return out


def _finance_periods(row: dict) -> dict:
    """기수명(예: '제 55 기')을 정규화 키로 모은다(빈 값 드롭)."""
    out: dict = {}
    for src, dst in _FINANCE_PERIOD_FIELDS.items():
        label = (row.get(src) or "").strip()
        if label:
            out[dst] = label
    return out


def _unique_account_key(table: dict, account_nm: str | None, account_detail: str | None) -> str:
    """
    account_nm을 키로 쓰되, 같은 표 안에서 충돌하면(자본변동표 등 매트릭스 계정)
    account_detail로 구분해 데이터 손실을 막는다. 그래도 겹치면 일련번호를 붙인다.
    """
    name = account_nm or "(이름없음)"
    if name not in table:
        return name
    detail = (account_detail or "").strip()
    if detail and detail != "-":
        qualified = f"{name} ({detail})"
        if qualified not in table:
            return qualified
        name = qualified
    idx = 2
    while f"{name} #{idx}" in table:
        idx += 1
    return f"{name} #{idx}"


def normalize_finances(rows: list[dict]) -> list[dict]:
    """
    재무제표 계정 행(1계정=1행)을 (회사·연도·보고서·재무제표구분) 단위 레코드로 묶는다.

    구조: statements(재무제표종류 sj_nm: 재무상태표/손익계산서/…) → account_nm → 기간별 금액.
      메타(corp·연도·보고서·통화·기수명)는 레코드 상단에 1번만 둔다.
      CFS(연결)·OFS(별도)는 같은 account_nm을 공유하므로 fs_div로 레코드를 분리한다.
    """
    grouped: dict[tuple, dict] = {}
    order: list[tuple] = []

    for row in rows:
        key = (
            row.get("corp_code"),
            row.get("bsns_year"),
            row.get("reprt_code"),
            row.get("fs_div"),
        )
        if key not in grouped:
            grouped[key] = {
                "corp_name":  row.get("corp_name"),
                "corp_code":  row.get("corp_code"),
                "bsns_year":  row.get("bsns_year"),
                "reprt_code": row.get("reprt_code"),
                "reprt_nm":   row.get("reprt_nm"),
                "fs_div":     row.get("fs_div"),
                "rcept_no":   row.get("rcept_no"),
                "currency":   row.get("currency"),
                "periods":    _finance_periods(row),
                "statements": {},
            }
            order.append(key)

        # 재무제표 종류(sj_nm)·계정명(account_nm)은 원천 한글 그대로 키로 쓴다.
        stmt_name = row.get("sj_nm") or row.get("sj_div") or "기타"
        table = grouped[key]["statements"].setdefault(stmt_name, {})
        acct_key = _unique_account_key(table, row.get("account_nm"), row.get("account_detail"))
        table[acct_key] = _finance_amounts(row)

    return [grouped[k] for k in order]
