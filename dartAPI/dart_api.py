"""
dart_api.py
DART(전자공시시스템) Open API 호출 모듈.
corp_code(8자리 고유번호)를 받아 회사 정보를 조회하는 함수 7개를 제공한다.
"""

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

def get_company(corp_code: str) -> dict | None:
    """
    기업 기본 정보(개황)를 조회한다.

    주요 반환 필드:
        corp_name   회사명
        ceo_nm      대표자명
        est_dt      설립일 (YYYYMMDD)
        induty_code 업종코드
        adres        주소
        hm_url      홈페이지 URL
        phn_no      전화번호
        fax_no      팩스번호

    Args:
        corp_code: DART 기업 고유번호 (8자리 문자열, 예: "00126380")

    Returns:
        조회 성공 시 응답 JSON 딕셔너리, 실패 시 None
    """
    print(f"[get_company] corp_code={corp_code}")
    return _get("company.json", {"corp_code": corp_code})


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


def get_litigation(
    corp_code: str,
    bgn_de: str,
    end_de: str,
) -> dict | None:
    """소송 등의 제기 내역(회사 리스크 파악용)을 기간으로 조회한다."""
    # 소송이 없는 기간에는 status "013" 이 정상적으로 반환될 수 있음
    print(f"[get_litigation] corp_code={corp_code}, 기간={bgn_de}~{end_de}")
    return _get("lwstLg.json", {
        "corp_code": corp_code,
        "bgn_de": bgn_de,
        "end_de": end_de,
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

    # 1. 기업 기본 정보
    result = get_company(SAMSUNG)
    pp("get_company — 삼성전자 기업개황", result)

    # 2. 최근 3개월 공시 목록 (2026-03-16 ~ 2026-06-15 기준)
    result = get_disclosures(SAMSUNG, bgn_de="20260316", end_de="20260615")
    pp("get_disclosures — 최근 3개월 공시 목록", result)

    # 3. 2024년 재무제표
    result = get_finance(SAMSUNG, bsns_year="2024")
    pp("get_finance — 2024년 사업보고서 재무제표", result)

    # 4. 2024년 직원 현황 (JSON 전체 출력으로 필드명 확인)
    result = get_employees(SAMSUNG, bsns_year="2024")
    pp("get_employees — 2024년 직원현황 (필드명 확인용 전체 출력)", result)

    # 5. 2024년 주요 재무지표 — 수익성(M210000) 기본값으로 조회
    result = get_financial_indicators(SAMSUNG, bsns_year="2024")
    pp("get_financial_indicators — 2024년 수익성 재무지표 (전체 출력)", result)

    # 6. 2024년 최대주주 현황
    result = get_major_shareholders(SAMSUNG, bsns_year="2024")
    pp("get_major_shareholders — 2024년 최대주주 현황 (전체 출력)", result)

    # 7. 소송 내역 — 2024년 한 해 기준
    result = get_litigation(SAMSUNG, bgn_de="20240101", end_de="20241231")
    pp("get_litigation — 2024년 소송 내역 (전체 출력)", result)
