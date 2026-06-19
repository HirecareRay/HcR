"""
collect.py
DART 데이터 수집 메인 실행 파일.

이 스크립트는 "정보 수집 + JSON 저장"만 담당한다.
수집한 데이터를 DB(SQLite 등)에 적재하는 일은 별도 담당자가
출력된 JSON 파일을 읽어 처리한다. (DB 연결 코드 없음)

수집 순서:
  1) 감사보고서       → 감사보고서.json
  2) 사업·반기보고서  → 재무_사업반기.json
  3) 분기보고서       → 재무_분기.json
  + 직원현황·재무지표 → 직원현황.json + 재무지표.json
  + 수집 실패 케이스  → 수집실패.json

저장 방식(메모리 누적 → 1회 기록):
  각 회사를 순회하며 결과를 메모리에 모은 뒤, 배치가 끝나면 JSON 파일을
  한 번만 통째로 기록한다(export_json.save_all). 도중에 중단돼도 다음 실행에서
  다시 수집하면 되며, JSON 배열([...]) 형태가 항상 온전하게 유지된다.

수집 기간 동적 계산:
  - 4개월 전 기준 연도의 재작년 1월 1일부터 오늘까지
  - 이유: 사업보고서가 익년 3월에 발행되므로, 4개월 전을 기준으로 삼으면
    연초(1~4월)에도 전전년도 사업보고서까지 안정적으로 수집 가능
  - 예) 2026-06: ref=2026 → start=2024-01-01
        2026-02: ref=2025 → start=2023-01-01
"""

import time
from collections import defaultdict
from datetime import datetime

import export_json
from corp_code import get_corp_code
from dart_api import (
    ANNUAL_SEMI_REPRT,
    API_CALL_DELAY,
    QUARTERLY_REPRT,
    get_employees,
    get_finance_range,
    get_financial_indicators,
)
from dart_audit import get_audit_reports, get_audit_text
from normalize import normalize_employees, normalize_finances, normalize_indicators

# DART 원문 보고서 직링크 — rcept_no로 원본 공시를 바로 열어볼 수 있다.
_DART_REPORT_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# 사업보고서 코드 — 직원현황·재무지표는 연간 사업보고서(11011) 기준으로 수집한다.
_ANNUAL_REPRT_CODE = "11011"

# 재무지표 분류 코드 ↔ 명칭. 취준생 관점에선 안정성(부채비율)·수익성(ROE)이 핵심이지만,
# 성장성·활동성까지 함께 담아 회사 상태를 입체적으로 보여준다.
_INDICATOR_CATEGORIES: dict[str, str] = {
    "M210000": "수익성",
    "M220000": "안정성",
    "M230000": "성장성",
    "M240000": "활동성",
}


def _calc_start_year(today: datetime) -> int:
    """
    오늘 기준 4개월 전 연도의 재작년을 반환한다.
    사업보고서가 익년 3월에 발행되므로 4개월을 빼서 기준 연도를 안정화한다.
    """
    ref_year = today.year if today.month > 4 else today.year - 1
    return ref_year - 2


def _print_finance_summary(label: str, finance_list: list[dict]) -> None:
    counts: dict[str, int] = defaultdict(int)
    for row in finance_list:
        key = f"{row.get('bsns_year')} {row.get('reprt_nm')}"
        counts[key] += 1
    summary = " / ".join(f"{k} → {v}건" for k, v in counts.items())
    print(f"\n[{label} 수집 요약] {summary if summary else '데이터 없음'}")


def _empty_result() -> dict:
    """수집 결과의 빈 골격. corp_code 조회 실패 등에서 사용한다."""
    return {
        "audit_reports":        [],
        "finances_annual_semi": [],
        "finances_quarterly":   [],
        "employees":            [],
        "financial_indicators": [],
        "failures":             [],
    }


def _collect_employees(
    corp_code: str, corp_name: str, start_year: int, end_year: int
) -> list[dict]:
    """
    연도별 직원현황(empSttus.json) 원본 행을 평면 리스트로 수집한다.
    (정규화는 collect()에서 normalize_employees가 담당 — 여기선 원본 보존)

    원본은 사업부문(fo_bbm)×성별(sexdstn)로 행이 쪼개져 온다.
    주요 필드: sm(직원수), sexdstn(성별), avrg_cnwk_sdytrn(평균 근속연수),
              jan_salary_am(1인 평균 급여액), fyer_salary_totamt(연간 급여 총액)
    데이터 없는 연도(status 013)는 건너뛴다.
    """
    rows: list[dict] = []
    for idx, year in enumerate(range(start_year, end_year + 1)):
        if idx > 0:
            time.sleep(API_CALL_DELAY)  # DART rate limit 보호
        data = get_employees(corp_code, str(year), _ANNUAL_REPRT_CODE)
        if not data or "list" not in data:
            continue
        for row in data["list"]:
            rows.append({"corp_name": corp_name, "bsns_year": str(year), **row})
    return rows


def _collect_indicators(
    corp_code: str, corp_name: str, start_year: int, end_year: int
) -> list[dict]:
    """
    연도별 주요 재무지표(fnlttSinglIndx.json) 원본 행을 4개 분류 전부 수집한다.
    (정규화는 collect()에서 normalize_indicators가 담당 — 여기선 원본 보존)

    DART가 이미 계산해 둔 지표(부채비율·ROE 등)라 취준생이 회사 안정성·수익성을
    바로 가늠할 수 있다. 원본은 지표 1개가 1행이다.
    데이터 없는 연도/분류(status 013)는 건너뛴다.
    """
    rows: list[dict] = []
    first = True
    for year in range(start_year, end_year + 1):
        for idx_code, idx_nm in _INDICATOR_CATEGORIES.items():
            if not first:
                time.sleep(API_CALL_DELAY)  # DART rate limit 보호
            first = False
            data = get_financial_indicators(
                corp_code, str(year), _ANNUAL_REPRT_CODE, idx_code
            )
            if not data or "list" not in data:
                continue
            for row in data["list"]:
                rows.append({
                    "corp_name":  corp_name,
                    "bsns_year":  str(year),
                    "idx_cl_nm":  idx_nm,   # 분류 명칭(수익성/안정성/성장성/활동성)
                    **row,
                })
    return rows


def collect(corp_name: str) -> dict:
    """
    회사명으로 DART 데이터를 수집해 카테고리별 레코드 dict로 반환한다.
    (DB에 저장하지 않는다 — 호출 측이 결과를 모아 JSON으로 기록한다)

    Returns:
        {
          "audit_reports":        list[dict],    # 감사보고서 (financial_tables 중첩 포함)
          "finances_annual_semi": list[dict],    # 사업·반기보고서 재무 계정 행
          "finances_quarterly":   list[dict],    # 분기보고서 재무 계정 행
          "employees":            list[dict],    # 직원현황 (연봉·직원수·근속)
          "financial_indicators": list[dict],    # 주요 재무지표 (부채비율·ROE 등)
          "failures":             list[dict],    # 수집 실패 케이스
        }
    """
    today        = datetime.today()
    start_year   = _calc_start_year(today)
    end_year     = today.year
    bgn_de       = f"{start_year}0101"
    end_de       = today.strftime("%Y%m%d")
    collected_at = today.strftime("%Y-%m-%d %H:%M")

    result = _empty_result()
    failures = result["failures"]

    # ── corp_code 변환 ───────────────────────────────────────────────────────
    corp_code = get_corp_code(corp_name)
    if not corp_code:
        print(f"[오류] '{corp_name}'의 고유번호를 찾지 못했습니다. 종료합니다.")
        failures.append({
            "collected_at":   collected_at,
            "corp_name":      corp_name,
            "fail_type":      "corp_code_not_found",
            "corp_code":      "",
            "rcept_no":       "",
            "report_nm":      "",
            "missing_fields": "",
            "dart_link":      "",
        })
        return result

    print(f"\n{'=' * 60}")
    print(f"  {corp_name} ({corp_code}) 데이터 수집 시작")
    print(f"  수집 기간: {bgn_de} ~ {end_de}  (start_year={start_year})")
    print(f"{'=' * 60}\n")

    # ────────────────────────────────────────────────────────────────────────
    # STEP 1. 감사보고서
    # ────────────────────────────────────────────────────────────────────────
    print(f"\n[STEP 1] 감사보고서 수집")
    audit_reports = get_audit_reports(corp_code)

    if audit_reports:
        for report in audit_reports:
            text_data = get_audit_text(report["rcept_no"])
            rcept_no  = report.get("rcept_no", "")
            record = {
                "corp_name": corp_name,
                "corp_code": corp_code,
                "dart_link": _DART_REPORT_URL.format(rcept_no=rcept_no) if rcept_no else "",
                **report,
                **text_data,
            }
            result["audit_reports"].append(record)

            # 재무수치 파싱 실패 감지
            missing = [
                k for k in ("revenue", "operating_income", "net_income")
                if text_data.get(k) is None
            ]
            if missing:
                failures.append({
                    "collected_at":   collected_at,
                    "corp_name":      corp_name,
                    "fail_type":      "audit_figure_missing",
                    "corp_code":      corp_code,
                    "rcept_no":       rcept_no,
                    "report_nm":      report.get("report_nm", ""),
                    "missing_fields": ",".join(missing),
                    "dart_link":      _DART_REPORT_URL.format(rcept_no=rcept_no) if rcept_no else "",
                })

        print(f"[STEP 1 완료] 감사보고서 {len(result['audit_reports'])}건 수집")
    else:
        print("[STEP 1] 감사보고서 데이터 없음")

    # ────────────────────────────────────────────────────────────────────────
    # STEP 2. 사업보고서 + 반기보고서 (reprt_code: 11011, 11012)
    # ────────────────────────────────────────────────────────────────────────
    print(f"\n[STEP 2] 사업보고서·반기보고서 수집")
    annual_list = get_finance_range(
        corp_code,
        start_year=start_year,
        end_year=end_year,
        reprt_map=ANNUAL_SEMI_REPRT,
    )

    if annual_list:
        # corp_name 주입(불변 패턴) 후 보고서 단위 k,v 레코드로 정규화한다.
        annual_named = [{"corp_name": corp_name, **row} for row in annual_list]
        result["finances_annual_semi"] = normalize_finances(annual_named)
        _print_finance_summary("사업·반기보고서", annual_list)
        print(f"[STEP 2] 계정 {len(annual_list)}행 → {len(result['finances_annual_semi'])}레코드")
    else:
        print("[STEP 2] 사업·반기보고서 재무 데이터 없음")

    # ────────────────────────────────────────────────────────────────────────
    # STEP 3. 분기보고서 (reprt_code: 11013, 11014)
    # ────────────────────────────────────────────────────────────────────────
    print(f"\n[STEP 3] 분기보고서 수집")
    quarterly_list = get_finance_range(
        corp_code,
        start_year=start_year,
        end_year=end_year,
        reprt_map=QUARTERLY_REPRT,
    )

    if quarterly_list:
        quarterly_named = [{"corp_name": corp_name, **row} for row in quarterly_list]
        result["finances_quarterly"] = normalize_finances(quarterly_named)
        _print_finance_summary("분기보고서", quarterly_list)
        print(f"[STEP 3] 계정 {len(quarterly_list)}행 → {len(result['finances_quarterly'])}레코드")
    else:
        print("[STEP 3] 분기보고서 재무 데이터 없음")

    # ────────────────────────────────────────────────────────────────────────
    # STEP 4. 취준생 관심 정보 — 직원현황(연봉·직원수) / 재무지표
    # ────────────────────────────────────────────────────────────────────────
    # 원본 행을 수집한 뒤 (회사·연도) 단위 k,v 레코드로 정규화한다.
    # (직원현황은 부문×성별로, 재무지표는 지표별로 행이 쪼개져 오므로 묶는다)
    print(f"\n[STEP 4] 직원현황·재무지표 수집")
    emp_rows = _collect_employees(corp_code, corp_name, start_year, end_year)
    result["employees"] = normalize_employees(emp_rows)
    print(f"[STEP 4] 직원현황 원본 {len(emp_rows)}행 → {len(result['employees'])}레코드")

    idx_rows = _collect_indicators(corp_code, corp_name, start_year, end_year)
    result["financial_indicators"] = normalize_indicators(idx_rows)
    print(f"[STEP 4] 재무지표 원본 {len(idx_rows)}행 → {len(result['financial_indicators'])}레코드")

    print(f"\n{'=' * 60}")
    print(f"  {corp_name} 데이터 수집 완료")
    print(f"{'=' * 60}\n")
    return result


if __name__ == "__main__":
    import sys
    from pathlib import Path

    companies_file = Path(__file__).parent / "companies.txt"

    if len(sys.argv) == 2 and Path(sys.argv[1]).suffix == ".txt":
        # 파일 경로를 인자로 받은 경우: python collect.py test_companies.txt
        companies_file = Path(sys.argv[1])
        if not companies_file.exists():
            print(f"[오류] {companies_file} 파일이 없습니다.")
            sys.exit(1)
        companies = [
            line.strip()
            for line in companies_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    elif len(sys.argv) > 1:
        # 회사명 직접 입력: python collect.py 삼성전자
        companies = [" ".join(sys.argv[1:])]
    elif companies_file.exists():
        companies = [
            line.strip()
            for line in companies_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        print(f"[오류] {companies_file} 파일이 없습니다.")
        sys.exit(1)

    total = len(companies)
    print(f"[일괄 수집 시작] 총 {total}개 회사")

    # 모든 회사의 결과를 카테고리별로 메모리에 누적한다(방법2).
    all_audit:       list[dict] = []
    all_annual_semi: list[dict] = []
    all_quarterly:   list[dict] = []
    all_employees:   list[dict] = []
    all_indicators:  list[dict] = []
    all_failures:    list[dict] = []
    errored: list[str] = []

    for idx, corp_name in enumerate(companies, start=1):
        print(f"\n[{idx}/{total}] {corp_name} 수집 시작")
        try:
            result = collect(corp_name)
            all_audit.extend(result["audit_reports"])
            all_annual_semi.extend(result["finances_annual_semi"])
            all_quarterly.extend(result["finances_quarterly"])
            all_employees.extend(result["employees"])
            all_indicators.extend(result["financial_indicators"])
            all_failures.extend(result["failures"])
        except Exception as e:
            print(f"[오류] {corp_name} 수집 실패: {e}")
            errored.append(corp_name)

    # 배치 종료 후 JSON 파일을 한 번만 통째로 기록한다.
    export_json.save_all(
        audit_reports=all_audit,
        finances_annual_semi=all_annual_semi,
        finances_quarterly=all_quarterly,
        employees=all_employees,
        financial_indicators=all_indicators,
        failures=all_failures,
    )

    print(f"\n{'=' * 60}")
    print(f"[일괄 수집 완료] 성공: {total - len(errored)}개 / 예외: {len(errored)}개")
    if errored:
        print(f"[예외 목록] {', '.join(errored)}")
    if all_failures:
        print(f"[실패 케이스] {len(all_failures)}건 → {export_json.FAILURES_JSON}")
    print(f"{'=' * 60}")
