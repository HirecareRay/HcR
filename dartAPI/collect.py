"""
collect.py
DART 데이터 수집 메인 실행 파일.
회사명 하나를 받아 API 호출 → DB 저장 파이프라인을 순서대로 실행한다.
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from corp_code import get_corp_code
from dart_api import get_company, get_disclosures, get_employees, get_finance
from dart_report import get_latest_report_rcept_no, get_report_text
from db import (
    engine,
    init_db,
    save_company,
    save_disclosures,
    save_employees,
    save_finances,
    save_report_text,
)


def collect(corp_name: str) -> None:
    """
    회사명으로 DART 데이터를 수집해 SQLite DB에 저장한다.

    실행 순서:
      1) corp_code 변환
      2) 기업 기본 정보 저장
      3) 최근 1년 공시 목록 저장
      4) 2024년 연결 손익계산서(CFS/IS) 저장
      5) 2024년 직원 현황 (성별합계) 저장
      6) 최신 사업보고서 원문 텍스트 저장
    """
    # ── 1. corp_code 변환 ────────────────────────────────────────────────────
    corp_code = get_corp_code(corp_name)
    if not corp_code:
        print(f"[오류] '{corp_name}'의 고유번호를 찾지 못했습니다. 종료합니다.")
        return

    print(f"\n{'=' * 60}")
    print(f"  {corp_name} ({corp_code}) 데이터 수집 시작")
    print(f"{'=' * 60}\n")

    init_db()  # 테이블이 없으면 생성, 있으면 유지

    with Session(engine) as session:
        # ── 2. 기업 기본 정보 ────────────────────────────────────────────────
        company_data = get_company(corp_code)
        if company_data:
            save_company(session, company_data)

        # ── 3. 공시 목록 (최근 1년) ──────────────────────────────────────────
        today = datetime.today()
        end_de = today.strftime("%Y%m%d")
        bgn_de = (today - timedelta(days=365)).strftime("%Y%m%d")

        disc_data = get_disclosures(corp_code, bgn_de=bgn_de, end_de=end_de)
        if disc_data and "list" in disc_data:
            save_disclosures(session, corp_code, disc_data["list"])
        else:
            print("[안내] 공시 목록 데이터가 없습니다.")

        # ── 4. 재무제표 (2024년, 연결재무제표 손익계산서만) ──────────────────
        finance_data = get_finance(corp_code, bsns_year="2024")
        if finance_data and "list" in finance_data:
            # CFS(연결) + IS(손익계산서) 행만 추출
            filtered_finance = [
                row for row in finance_data["list"]
                if row.get("fs_div") == "CFS" and row.get("sj_div") == "IS"
            ]
            save_finances(session, corp_code, filtered_finance)
        else:
            print("[안내] 재무제표 데이터가 없습니다.")

        # ── 5. 직원 현황 (2024년, 성별합계 행만) ────────────────────────────
        emp_data = get_employees(corp_code, bsns_year="2024")
        if emp_data and "list" in emp_data:
            # fo_bbm(사업부문) == "성별합계" 인 행만 추출
            filtered_emp = [
                row for row in emp_data["list"]
                if row.get("fo_bbm") == "성별합계"
            ]
            save_employees(session, corp_code, filtered_emp)
        else:
            print("[안내] 직원 현황 데이터가 없습니다.")

        # ── 6. 사업보고서 원문 텍스트 ────────────────────────────────────────
        rcept_no = get_latest_report_rcept_no(corp_code)
        if rcept_no:
            texts = get_report_text(rcept_no)  # 기본: ["사업의 내용", "이사의 경영진단"]
            for section_nm, content in texts.items():
                if content:
                    save_report_text(session, corp_code, rcept_no, section_nm, content)
        else:
            print("[안내] 최신 사업보고서 접수번호를 찾지 못했습니다.")

        session.commit()  # 모든 저장을 한 번에 커밋

    print(f"\n{'=' * 60}")
    print(f"  {corp_name} 데이터 수집 완료")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        corp_name = " ".join(sys.argv[1:])
    else:
        corp_name = input("회사명을 입력하세요: ").strip()

    if not corp_name:
        print("[오류] 회사명을 입력해야 합니다.")
        sys.exit(1)

    collect(corp_name)
