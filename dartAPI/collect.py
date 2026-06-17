"""
collect.py
DART 데이터 수집 메인 실행 파일.

수집 순서:
  1) 감사보고서       → audit_reports 테이블 + 감사보고서.csv
  2) 사업·반기보고서  → finances 테이블 + 재무_사업반기.csv + reports 테이블
  3) 분기보고서       → finances 테이블 + 재무_분기.csv

수집 기간 동적 계산:
  - 4개월 전 기준 연도의 재작년 1월 1일부터 오늘까지
  - 이유: 사업보고서가 익년 3월에 발행되므로, 4개월 전을 기준으로 삼으면
    연초(1~4월)에도 전전년도 사업보고서까지 안정적으로 수집 가능
  - 예) 2026-06: ref=2026 → start=2024-01-01
        2026-02: ref=2025 → start=2023-01-01
"""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from corp_code import get_corp_code
from dart_api import ANNUAL_SEMI_REPRT, QUARTERLY_REPRT, get_company, get_finance_range
from dart_audit import get_audit_reports, get_audit_text, save_audit_csv
from dart_report import get_latest_report_rcept_no, get_report_text
from db import (
    _DART_REPORT_URL,
    engine,
    init_db,
    save_audit,
    save_company,
    save_failures_csv,
    save_finances,
    save_finances_csv,
    save_quarterly_csv,
    save_report_text,
)


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


def collect(corp_name: str) -> list[dict]:
    """
    회사명으로 DART 데이터를 수집해 SQLite DB 및 CSV에 저장한다.

    Returns:
        수집 중 발생한 실패 케이스 목록 (save_failures_csv 형식)
    """
    today      = datetime.today()
    start_year = _calc_start_year(today)
    end_year   = today.year
    bgn_de     = f"{start_year}0101"
    end_de     = today.strftime("%Y%m%d")
    collected_at = today.strftime("%Y-%m-%d %H:%M")
    failures: list[dict] = []

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
        return failures

    print(f"\n{'=' * 60}")
    print(f"  {corp_name} ({corp_code}) 데이터 수집 시작")
    print(f"  수집 기간: {bgn_de} ~ {end_de}  (start_year={start_year})")
    print(f"{'=' * 60}\n")

    init_db()

    with Session(engine) as session:
        # ── 기업 기본 정보 ───────────────────────────────────────────────────
        company_data = get_company(corp_code)
        if company_data:
            save_company(session, company_data)

        # ────────────────────────────────────────────────────────────────────
        # STEP 1. 감사보고서
        # ────────────────────────────────────────────────────────────────────
        print(f"\n[STEP 1] 감사보고서 수집")
        audit_reports = get_audit_reports(corp_code)

        if audit_reports:
            audit_records = []
            for report in audit_reports:
                text_data = get_audit_text(report["rcept_no"])
                record = {**report, **text_data}
                save_audit(session, corp_code, record)
                audit_records.append(record)

                # 재무수치 파싱 실패 감지
                missing = [
                    k for k in ("revenue", "operating_income", "net_income")
                    if text_data.get(k) is None
                ]
                if missing:
                    rcept_no = report.get("rcept_no", "")
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

            save_audit_csv(audit_records, corp_name)
            print(f"[STEP 1 완료] 감사보고서 {len(audit_records)}건 저장")
        else:
            print("[STEP 1] 감사보고서 데이터 없음")

        # ────────────────────────────────────────────────────────────────────
        # STEP 2. 사업보고서 + 반기보고서 (reprt_code: 11011, 11012)
        # ────────────────────────────────────────────────────────────────────
        print(f"\n[STEP 2] 사업보고서·반기보고서 수집")
        annual_list = get_finance_range(
            corp_code,
            start_year=start_year,
            end_year=end_year,
            reprt_map=ANNUAL_SEMI_REPRT,
        )

        if annual_list:
            save_finances(session, corp_code, annual_list)
            save_finances_csv(annual_list, corp_name)
            _print_finance_summary("사업·반기보고서", annual_list)

            # 사업보고서 원문 텍스트 (사업의 내용, 이사의 경영진단)
            rcept_no = get_latest_report_rcept_no(corp_code)
            if rcept_no:
                texts = get_report_text(rcept_no)
                for section_nm, content in texts.items():
                    if content:
                        save_report_text(session, corp_code, rcept_no, section_nm, content)
            else:
                print("[STEP 2] 최신 사업보고서 접수번호 없음")
        else:
            print("[STEP 2] 사업·반기보고서 재무 데이터 없음")

        # ────────────────────────────────────────────────────────────────────
        # STEP 3. 분기보고서 (reprt_code: 11013, 11014)
        # ────────────────────────────────────────────────────────────────────
        print(f"\n[STEP 3] 분기보고서 수집")
        quarterly_list = get_finance_range(
            corp_code,
            start_year=start_year,
            end_year=end_year,
            reprt_map=QUARTERLY_REPRT,
        )

        if quarterly_list:
            save_finances(session, corp_code, quarterly_list)
            save_quarterly_csv(quarterly_list, corp_name)
            _print_finance_summary("분기보고서", quarterly_list)
        else:
            print("[STEP 3] 분기보고서 재무 데이터 없음")

        session.commit()

    print(f"\n{'=' * 60}")
    print(f"  {corp_name} 데이터 수집 완료")
    print(f"{'=' * 60}\n")
    return failures


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

    all_failures: list[dict] = []
    errored: list[str] = []
    for idx, corp_name in enumerate(companies, start=1):
        print(f"\n[{idx}/{total}] {corp_name} 수집 시작")
        try:
            failures = collect(corp_name)
            all_failures.extend(failures)
        except Exception as e:
            print(f"[오류] {corp_name} 수집 실패: {e}")
            errored.append(corp_name)

    # 실패 케이스 CSV 저장
    save_failures_csv(all_failures)

    print(f"\n{'=' * 60}")
    print(f"[일괄 수집 완료] 성공: {total - len(errored)}개 / 예외: {len(errored)}개")
    if errored:
        print(f"[예외 목록] {', '.join(errored)}")
    if all_failures:
        from db import FAILURES_CSV
        print(f"[실패 케이스] {len(all_failures)}건 → {FAILURES_CSV}")
    print(f"{'=' * 60}")
