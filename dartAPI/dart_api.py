"""
dart_api.py
DART(전자공시시스템) Open API 호출 모듈.
corp_code(8자리 고유번호)를 받아 회사 정보를 조회하는 함수 7개를 제공한다.
"""

import time

import requests
from config import DART_API_KEY  # 인증키는 config.py에서 가져옴

# ── 상수 ─────────────────────────────────────────────────────────────────────
BASE_URL = "https://opendart.fss.or.kr/api"


# ── 내부 유틸 ─────────────────────────────────────────────────────────────────

def _get(endpoint: str, params: dict) -> dict | None:
    """
    DART API에 GET 요청을 보내고 JSON을 반환하는 내부 공통 함수.

    - HTTP 오류(4xx, 5xx) 발생 시 예외를 그대로 올림
    - DART 응답 status가 "000"(정상)이 아니면 에러 메시지를 출력하고 None 반환
    - 정상이면 응답 JSON 딕셔너리 전체를 반환
    """
    # 인증키는 모든 요청에 공통으로 추가
    params["crtfc_key"] = DART_API_KEY

    response = requests.get(
        f"{BASE_URL}/{endpoint}",
        params=params,
        timeout=30,
    )
    response.raise_for_status()  # HTTP 오류 시 예외 발생

    data = response.json()

    # DART는 정상일 때 status "000" 반환, 그 외는 비정상
    # "013" = 조회 데이터 없음 — 데이터가 없는 회사에서는 정상적으로 발생할 수 있음
    if data.get("status") == "013":
        print(f"[DART 안내] status=013 — 조회된 데이터가 없습니다. (해당 기간·보고서에 데이터가 없는 정상 케이스일 수 있습니다)")
        return None
    if data.get("status") != "000":
        print(f"[DART 오류] status={data.get('status')} message={data.get('message')}")
        return None

    return data


# ── 공개 함수 ─────────────────────────────────────────────────────────────────

def get_disclosures(corp_code: str, bgn_de: str, end_de: str) -> dict | None:
    """
    기간 내 공시 목록을 조회한다.

    주요 반환 필드 (data["list"] 안의 각 항목):
        rcept_no    접수번호
        corp_name   회사명
        report_nm   보고서명
        rcept_dt    접수일자 (YYYYMMDD)
        flr_nm      공시 제출인명

    Args:
        corp_code: DART 기업 고유번호 (8자리 문자열)
        bgn_de:    조회 시작일 (YYYYMMDD, 예: "20241001")
        end_de:    조회 종료일 (YYYYMMDD, 예: "20241231")

    Returns:
        조회 성공 시 응답 JSON 딕셔너리, 실패 시 None
    """
    print(f"[get_disclosures] corp_code={corp_code}, 기간={bgn_de}~{end_de}")
    return _get("list.json", {
        "corp_code": corp_code,
        "bgn_de": bgn_de,
        "end_de": end_de,
    })


def get_finance(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
) -> dict | None:
    """
    주요 재무제표 단일 계정을 조회한다.

    주요 반환 필드 (data["list"] 안의 각 항목):
        account_nm      계정명 (예: 매출액, 영업이익)
        thstrm_amount   당기금액
        frmtrm_amount   전기금액
        bfefrmtrm_amount 전전기금액
        fs_div          재무제표 구분 (CFS: 연결, OFS: 별도)
        sj_div          재무제표 종류 (BS: 재무상태표, IS: 손익계산서 등)

    Args:
        corp_code:   DART 기업 고유번호 (8자리 문자열)
        bsns_year:   사업연도 (4자리, 예: "2024")
        reprt_code:  보고서 코드
                       "11011" 사업보고서 (기본값)
                       "11012" 반기보고서
                       "11013" 1분기보고서
                       "11014" 3분기보고서

    Returns:
        조회 성공 시 응답 JSON 딕셔너리, 실패 시 None
    """
    print(f"[get_finance] corp_code={corp_code}, year={bsns_year}, reprt={reprt_code}")
    return _get("fnlttSinglAcnt.json", {
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "reprt_code": reprt_code,
    })


def get_finance_all(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
    fs_div: str = "CFS",
) -> dict | None:
    """
    전체 계정과목 재무제표를 조회한다 (fnlttSinglAcntAll.json).

    주요 반환 필드 (data["list"] 안의 각 항목):
        account_nm       계정명
        thstrm_amount    당기금액
        frmtrm_amount    전기금액
        bfefrmtrm_amount 전전기금액
        fs_div           재무제표 구분 (CFS: 연결, OFS: 별도)
        sj_div           재무제표 종류 (BS/IS/CIS/SCE/CF)

    Args:
        corp_code:   DART 기업 고유번호 (8자리 문자열)
        bsns_year:   사업연도 (4자리, 예: "2024")
        reprt_code:  보고서 코드
                       "11011" 사업보고서 (기본값)
                       "11012" 반기보고서
                       "11013" 1분기보고서
                       "11014" 3분기보고서
        fs_div:      재무제표 구분 (CFS: 연결 기본값, OFS: 별도)

    Returns:
        조회 성공 시 응답 JSON 딕셔너리, 실패 시 None
    """
    print(f"[get_finance_all] corp_code={corp_code}, year={bsns_year}, reprt={reprt_code}, fs_div={fs_div}")
    return _get("fnlttSinglAcntAll.json", {
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "reprt_code": reprt_code,
        "fs_div": fs_div,
    })


# ── 보고서 코드 상수 ─────────────────────────────────────────────────────────
# 사업보고서 + 반기보고서: 연간·반기 실적 확인 (step 2)
ANNUAL_SEMI_REPRT: dict[str, str] = {
    "11011": "사업보고서",   # 연간 전체 실적 (가장 중요)
    "11012": "반기보고서",   # 상반기(1~6월) 누적 실적
}

# 분기보고서: 분기별 세부 실적 (step 3)
QUARTERLY_REPRT: dict[str, str] = {
    "11013": "1분기보고서",  # 1분기(1~3월) 누적 실적
    "11014": "3분기보고서",  # 3분기(1~9월) 누적 실적 (2분기는 반기보고서가 대체)
}

# 재무제표 구분: 두 값을 모두 조회해야 재무제표가 완결된다.
#   OFS = 별도/개별재무제표, CFS = 연결재무제표
# 종속회사가 없는 회사는 CFS가 없어 status "013"(데이터 없음)이 반환되므로
# 해당 fs_div는 건너뛴다. (하드코딩 방지를 위해 상수 리스트로 분리)
FS_DIV_LIST: list[str] = ["OFS", "CFS"]

# fnlttSinglAcntAll 연속 호출 사이의 딜레이(초). fs_div 두 값을 반복 호출하면서
# 호출 수가 2배가 되므로 DART rate limit 보호용으로 짧은 간격을 둔다.
API_CALL_DELAY: float = 0.2

# fnlttSinglAcntAll.json 응답 원본 필드 21개 — 수집 시 하나도 버리지 않고 전부 보존한다.
# 특히 다음 두 필드는 절대 누락시키지 않는다(하드코딩 방지를 위해 상수 리스트로 분리):
#   rcept_no  : 접수번호 — 원본 공시문서 역추적 + 데이터 검증의 유일한 키
#   corp_code : 고유번호 — 회사 식별자(회사명보다 안정적, 동명·사명변경 대비)
DART_FINANCE_FIELDS: list[str] = [
    "rcept_no", "reprt_code", "bsns_year", "corp_code",
    "sj_div", "sj_nm", "account_id", "account_nm", "account_detail",
    "thstrm_nm", "thstrm_amount", "thstrm_add_amount",
    "frmtrm_nm", "frmtrm_amount", "frmtrm_q_nm", "frmtrm_q_amount",
    "frmtrm_add_amount", "bfefrmtrm_nm", "bfefrmtrm_amount",
    "ord", "currency",
]

# 수집 측에서 각 계정 행에 덧붙이는 메타 필드 (get_finance_range가 주입).
# corp_name은 CSV 기록 시점에 별도 주입되므로 여기 포함하지 않는다.
COLLECTED_FINANCE_FIELDS: list[str] = ["fs_div", "reprt_nm"]


def get_finance_range(
    corp_code: str,
    start_year: int,
    end_year: int,
    reprt_map: dict[str, str] | None = None,
) -> list[dict]:
    """
    start_year ~ end_year 각 연도별로 지정된 보고서 유형의 전체 계정과목을 조회해
    1계정 = 1행인 평면(flat) 리스트로 반환한다.
    FS_DIV_LIST(OFS 별도/개별, CFS 연결)를 모두 반복 호출해 두 재무제표를 함께 담는다.
    종속회사가 없어 CFS가 없는 회사 등은 해당 fs_div에서 데이터 없음(status 013)이
    반환되며, 에러 없이 건너뛴다.

    reprt_map 미지정 시 ANNUAL_SEMI_REPRT + QUARTERLY_REPRT 전체 조회.

    각 행은 fnlttSinglAcntAll.json 원본 필드(rcept_no, sj_div, sj_nm, account_id,
    account_nm, account_detail, ord, currency, thstrm_*, frmtrm_*, bfefrmtrm_* 등)에
    더해 호출 시점에 결정된 fs_div(CFS/OFS)와 reprt_nm(보고서명)을 추가로 담는다.

    반환 예:
        [
            {
                "rcept_no": "20250311001085", "bsns_year": "2024",
                "reprt_code": "11011", "reprt_nm": "사업보고서", "fs_div": "CFS",
                "sj_div": "BS", "sj_nm": "재무상태표",
                "account_id": "ifrs-full_Assets", "account_nm": "자산총계",
                "ord": "7", "currency": "KRW",
                "thstrm_amount": "514531948000000", "frmtrm_amount": "455905980000000",
                ...
            },
            ...
        ]

    Args:
        corp_code:  DART 기업 고유번호 (8자리 문자열)
        start_year: 수집 시작 연도
        end_year:   수집 종료 연도 (포함)
        reprt_map:  조회할 보고서 코드 딕셔너리 (None이면 전체 조회)

    Returns:
        연도·보고서·계정 단위 dict 리스트. 데이터 없는 연도/보고서는 건너뜀.
    """
    if reprt_map is None:
        reprt_map = {**ANNUAL_SEMI_REPRT, **QUARTERLY_REPRT}

    results: list[dict] = []

    for year in range(start_year, end_year + 1):
        bsns_year = str(year)

        for reprt_code, reprt_nm in reprt_map.items():
            # OFS(별도)·CFS(연결) 두 재무제표를 각각 조회해 함께 누적한다.
            for idx, fs_div in enumerate(FS_DIV_LIST):
                # 첫 호출이 아니면 rate limit 보호용 딜레이
                if idx > 0:
                    time.sleep(API_CALL_DELAY)

                print(f"[get_finance_range] {bsns_year} {reprt_nm} ({fs_div}) 조회 중...")
                data = get_finance_all(
                    corp_code, bsns_year=bsns_year, reprt_code=reprt_code, fs_div=fs_div
                )

                # status "000"이 아니면(013 데이터 없음 등) _get이 None 반환 → 건너뜀
                if data is None or "list" not in data:
                    print(f"[get_finance_range] {bsns_year} {reprt_nm} ({fs_div}) — 데이터 없음, 건너뜀")
                    continue

                # 동일 (fs_div, sj_div, account_id, account_nm, account_detail) 중복 계정은
                # 첫 번째만 사용한다.
                #   - fs_div: OFS/CFS 간 동일 계정이 서로 지워지지 않도록 포함
                #   - account_detail: 자본변동표(SCE)는 자본금·자본잉여금·이익잉여금·…·총계가
                #     "열"로 나열된 매트릭스를 DART가 행으로 평면화하므로, 같은 account_nm·
                #     account_id("-표준계정코드 미사용-")가 자본 구성요소별로 여러 번 등장한다.
                #     이 행들은 account_detail(예: "자본 [구성요소]|자본잉여금 [구성요소]")로만
                #     구분되므로, account_detail을 키에서 빼면 첫 행(주로 thstrm_amount가 빈
                #     자본금 구성요소)만 남고 값이 있는 다른 구성요소 행이 통째로 버려진다.
                #     BS/IS/CF는 account_detail이 단일값이라 키에 추가해도 영향이 없다.
                seen: set[tuple] = set()
                added = 0
                for row in data["list"]:
                    key = (
                        fs_div,
                        row.get("sj_div"),
                        row.get("account_id"),
                        row.get("account_nm"),
                        row.get("account_detail"),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    # 원본 행(DART_FINANCE_FIELDS 21개)을 그대로 보존한 새 dict 생성
                    # (불변 패턴) + 호출 메타(fs_div, reprt_nm) 주입. 어떤 필드도 필터링하지 않는다.
                    results.append({**row, "fs_div": fs_div, "reprt_nm": reprt_nm})
                    added += 1

                print(f"[get_finance_range] {bsns_year} {reprt_nm} ({fs_div}) → {added}계정")

    return results


def get_employees(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
) -> dict | None:
    """
    직원 현황을 조회한다.

    ※ 실제 응답 필드명 확인을 위해 JSON 전체를 출력한다.
    (DART 문서 기준 예상 필드: fo_bbm, sexdstn, reform_rsn, january_smcmp_fprr_january_smcmp_fprr,
     avg_cnwk_sdytrn, jan_salary_am 등 — 실행 후 출력으로 직접 확인할 것)

    Args:
        corp_code:   DART 기업 고유번호 (8자리 문자열)
        bsns_year:   사업연도 (4자리, 예: "2024")
        reprt_code:  보고서 코드 (get_finance와 동일 기준)

    Returns:
        조회 성공 시 응답 JSON 딕셔너리, 실패 시 None
    """
    print(f"[get_employees] corp_code={corp_code}, year={bsns_year}, reprt={reprt_code}")
    return _get("empSttus.json", {
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "reprt_code": reprt_code,
    })


def get_financial_indicators(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
    idx_cl_code: str = "M210000",
) -> dict | None:
    """단일회사 주요 재무지표(ROE, 부채비율 등 이미 계산된 지표)를 조회한다."""
    # idx_cl_code 분류: M210000 수익성 / M220000 안정성 / M230000 성장성 / M240000 활동성
    print(f"[get_financial_indicators] corp_code={corp_code}, year={bsns_year}, reprt={reprt_code}, idx_cl={idx_cl_code}")
    return _get("fnlttSinglIndx.json", {
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "reprt_code": reprt_code,
        "idx_cl_code": idx_cl_code,
    })


def get_major_shareholders(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
) -> dict | None:
    """최대주주 현황(지배구조 파악용 — 이름·지분율)을 조회한다."""
    # 데이터가 없는 회사에서는 status "013" 이 정상적으로 반환될 수 있음
    print(f"[get_major_shareholders] corp_code={corp_code}, year={bsns_year}, reprt={reprt_code}")
    return _get("hyslrSttus.json", {
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "reprt_code": reprt_code,
    })


# ── 직접 실행 시 테스트 ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    SAMSUNG = "00126380"  # 삼성전자 고유번호

    def pp(label: str, data: dict | None) -> None:
        """결과를 보기 좋게 출력하는 헬퍼."""
        print(f"\n{'=' * 60}")
        print(f"  {label}")
        print("=" * 60)
        if data is None:
            print("  → 결과 없음 (None)")
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))

    # 1. 최근 3개월 공시 목록 (2026-03-16 ~ 2026-06-15 기준)
    result = get_disclosures(SAMSUNG, bgn_de="20260316", end_de="20260615")
    pp("get_disclosures — 최근 3개월 공시 목록", result)

    # 3. 2024년 재무제표 (단일 호출)
    result = get_finance(SAMSUNG, bsns_year="2024")
    pp("get_finance — 2024년 사업보고서 재무제표", result)

    # 3-1. 3년치 사업보고서+반기보고서+분기보고서 범위 수집 테스트
    from datetime import date
    today = date.today()
    start_year = today.year - 2  # 재작년부터
    print(f"\n{'=' * 60}")
    print(f"  get_finance_range 테스트 ({start_year} ~ {today.year})")
    print("=" * 60)
    range_result = get_finance_range(SAMSUNG, start_year=start_year, end_year=today.year)
    # 연도/보고서별 계정 수 요약 (평면 리스트 → 그룹 집계)
    from collections import defaultdict
    summary: dict[tuple, int] = defaultdict(int)
    for row in range_result:
        summary[(row["bsns_year"], row["reprt_nm"], row["fs_div"])] += 1
    for (year, nm, fs_div), cnt in sorted(summary.items()):
        print(f"  {year} {nm} ({fs_div}) → {cnt}계정")
    if not range_result:
        print("[결과] 데이터 없음")

    # 4. 2024년 직원 현황 (JSON 전체 출력으로 필드명 확인)
    result = get_employees(SAMSUNG, bsns_year="2024")
    pp("get_employees — 2024년 직원현황 (필드명 확인용 전체 출력)", result)

    # 5. 2024년 주요 재무지표 — 수익성(M210000) 기본값으로 조회
    result = get_financial_indicators(SAMSUNG, bsns_year="2024")
    pp("get_financial_indicators — 2024년 수익성 재무지표 (전체 출력)", result)

    # 6. 2024년 최대주주 현황
    result = get_major_shareholders(SAMSUNG, bsns_year="2024")
    pp("get_major_shareholders — 2024년 최대주주 현황 (전체 출력)", result)
