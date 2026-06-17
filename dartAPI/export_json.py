"""
export_json.py
수집한 DART 데이터를 JSON 파일로 저장하는 모듈.

이 모듈은 DB(SQLite/SQLAlchemy)에 의존하지 않는다.
collect.py는 "정보 수집 + JSON 저장"만 담당하고, 수집된 JSON을 DB에
적재하는 일은 별도 담당자가 처리한다.

저장 방식(메모리 누적 → 1회 기록):
  collect.py가 모든 회사를 순회하며 결과를 메모리에 누적한 뒤,
  배치 종료 시점에 각 JSON 파일을 한 번만 통째로 기록한다(덮어쓰기).
  → append 시 배열을 매번 다시 읽고 합칠 필요가 없어 단순하고 빠르다.

깨짐 방지:
  - ensure_ascii=False  → 한글을 유니코드 이스케이프 없이 그대로 보존
  - indent=2            → 사람이 읽기 좋은 들여쓰기
  - 중첩 구조(financial_tables 등)는 문자열이 아니라 객체 그대로 저장
"""

import json
import warnings
from pathlib import Path

DATA_DIR = Path("data")

# 출력 파일 경로 — 테이블/도메인 단위로 분리해 DB 담당자가 적재하기 쉽게 한다.
COMPANIES_JSON          = DATA_DIR / "기업정보.json"
AUDIT_JSON              = DATA_DIR / "감사보고서.json"
FINANCES_JSON           = DATA_DIR / "재무_사업반기.json"
FINANCES_QUARTERLY_JSON = DATA_DIR / "재무_분기.json"
REPORT_TEXTS_JSON       = DATA_DIR / "사업보고서원문.json"
# 취준생 관심 정보 — 연봉·직원수·근속(직원현황), 부채비율·ROE(재무지표), 회사 리스크(소송)
EMPLOYEES_JSON          = DATA_DIR / "직원현황.json"
INDICATORS_JSON         = DATA_DIR / "재무지표.json"
LITIGATION_JSON         = DATA_DIR / "소송내역.json"
FAILURES_JSON           = DATA_DIR / "수집실패.json"


def write_json(records: list[dict], path: Path) -> None:
    """
    레코드 리스트를 JSON 배열([...])로 파일에 통째로 기록한다(덮어쓰기).

    records가 비어 있으면 빈 배열([])을 기록해 "수집 결과 없음"을 명시한다.
    저장 실패 시 예외를 삼키지 않고 경고로 알린다(데이터 유실 방지).
    """
    DATA_DIR.mkdir(exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        print(f"[export_json] {path} → {len(records)}건 저장")
    except Exception as exc:
        warnings.warn(f"[경고] JSON 저장 실패 ({path}): {exc}")


def save_all(
    *,
    companies: list[dict],
    audit_reports: list[dict],
    finances_annual_semi: list[dict],
    finances_quarterly: list[dict],
    report_texts: list[dict],
    employees: list[dict],
    financial_indicators: list[dict],
    litigation: list[dict],
    failures: list[dict],
) -> None:
    """수집 결과 9종을 각각의 JSON 파일로 한 번에 기록한다."""
    write_json(companies,            COMPANIES_JSON)
    write_json(audit_reports,        AUDIT_JSON)
    write_json(finances_annual_semi, FINANCES_JSON)
    write_json(finances_quarterly,   FINANCES_QUARTERLY_JSON)
    write_json(report_texts,         REPORT_TEXTS_JSON)
    write_json(employees,            EMPLOYEES_JSON)
    write_json(financial_indicators, INDICATORS_JSON)
    write_json(litigation,           LITIGATION_JSON)
    write_json(failures,             FAILURES_JSON)
