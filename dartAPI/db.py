"""
db.py
SQLAlchemy를 사용한 SQLite DB 초기화 및 저장 함수 모음.
추후 MySQL로 교체 시 ENGINE_URL 한 줄만 변경하면 된다.
"""

import csv
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session

from dart_api import DART_FINANCE_FIELDS

# ── DB 설정 ───────────────────────────────────────────────────────────────────
DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "dart.db"
ENGINE_URL = f"sqlite:///{DB_PATH}"  # MySQL 전환 시 이 줄만 변경

engine = create_engine(ENGINE_URL, echo=False)


# ── ORM 모델 ──────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    corp_code   = Column(String, primary_key=True)
    corp_name   = Column(String)
    ceo_nm      = Column(String)
    est_dt      = Column(String)
    induty_code = Column(String)
    adres       = Column(String)
    hm_url      = Column(String)
    stock_code  = Column(String)


class Disclosure(Base):
    __tablename__ = "disclosures"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    corp_code = Column(String, ForeignKey("companies.corp_code"))
    report_nm = Column(String)
    rcept_dt  = Column(String)
    rcept_no  = Column(String)
    flr_nm    = Column(String)


class Finance(Base):
    """
    전체 계정과목 재무제표(fnlttSinglAcntAll.json) 1계정 = 1행으로 저장한다.

    upsert 기준: corp_code + bsns_year + reprt_code 조합
      → 같은 보고서를 다시 수집하면 해당 조합 행(CFS·OFS 모두)을 지우고 재삽입한다.
      → CFS/OFS는 fs_div 컬럼으로 구분되며, 같은 data_list에 함께 담겨 저장되므로
        재수집 시에도 중복 없이 멱등하게 동작한다.

    ※ DB 레벨 UNIQUE(corp_code, bsns_year, reprt_code, fs_div, account_id)는
      적용하지 않는다. DART가 비표준 계정에 account_id="-표준계정코드 미사용-"를
      한 보고서에 여러 번 부여해 account_id가 보고서 내에서 유일하지 않기 때문이다.
      중복 방지는 위의 (조합 통삭제 → 재삽입) 멱등 처리로 보장한다.
    """
    __tablename__ = "finances"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    corp_code         = Column(String, ForeignKey("companies.corp_code"))
    # 원문 접수번호 — DART 보고서 직링크/추적용. 공시 단위 조회·검증에 자주 쓰므로 인덱스.
    rcept_no          = Column(String, index=True)
    bsns_year         = Column(String)
    # reprt_code: 11011 사업 / 11012 반기 / 11013 1분기 / 11014 3분기
    reprt_code        = Column(String)
    reprt_nm          = Column(String)
    # fs_div: CFS(연결) / OFS(별도)
    fs_div            = Column(String)
    # sj_div: BS 재무상태표 / IS 손익 / CIS 포괄손익 / SCE 자본변동 / CF 현금흐름
    sj_div            = Column(String)
    sj_nm             = Column(String)
    # account_id: XBRL 표준 계정 ID (예: ifrs-full_Assets)
    account_id        = Column(String)
    account_nm        = Column(String)
    account_detail    = Column(String)
    # ord: 보고서 내 계정 정렬순서
    ord               = Column(Integer)
    currency          = Column(String)
    # 당기 (제56기) — thstrm_add_amount는 분기·반기 누적금액
    thstrm_nm         = Column(String)
    thstrm_amount     = Column(BigInteger)
    thstrm_add_amount = Column(BigInteger)
    # 전기 (제55기)
    frmtrm_nm         = Column(String)
    frmtrm_amount     = Column(BigInteger)
    # 전기 동분기 (분기·반기 보고서에만 존재)
    frmtrm_q_nm       = Column(String)
    frmtrm_q_amount   = Column(BigInteger)
    frmtrm_add_amount = Column(BigInteger)
    # 전전기 (제54기 — 연간 보고서에만 존재)
    bfefrmtrm_nm      = Column(String)
    bfefrmtrm_amount  = Column(BigInteger)


class Employee(Base):
    __tablename__ = "employees"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    corp_code          = Column(String, ForeignKey("companies.corp_code"))
    stlm_dt            = Column(String)
    fo_bbm             = Column(String)
    sexdstn            = Column(String)
    sm                 = Column(Integer)
    avrg_cnwk_sdytrn   = Column(String)
    jan_salary_am      = Column(BigInteger)
    fyer_salary_totamt = Column(BigInteger)


class AuditReport(Base):
    __tablename__ = "audit_reports"

    # [사용] id: SQLite 자동 증가 기본키
    id             = Column(Integer, primary_key=True, autoincrement=True)
    # [사용] corp_code: companies 테이블과 연결 → 기업 정보와 함께 조회 가능
    corp_code      = Column(String, ForeignKey("companies.corp_code"))
    # [사용] rcept_no: DART 원문 접근 키 + 중복 저장 방지 식별자
    rcept_no       = Column(String)
    # [사용] report_nm: 보고서명 (어떤 연도 감사보고서인지 팀원 확인용)
    report_nm      = Column(String)
    # [사용] rcept_dt: 접수일자 (최신 여부 판단 + 정렬 기준)
    rcept_dt       = Column(String)
    # "적정"/"한정"/"부적정"/"의견거절"/"확인불가" 키워드
    audit_opinion  = Column(String)
    # 핵심감사사항 최대 500자
    key_audit      = Column(Text)
    created_at     = Column(String)


# ── DB 초기화 ─────────────────────────────────────────────────────────────────

def init_db() -> None:
    """data/ 폴더와 dart.db 파일을 만들고 테이블을 생성한다 (이미 있으면 유지)."""
    DATA_DIR.mkdir(exist_ok=True)
    Base.metadata.create_all(engine)
    # rcept_no 인덱스 — 공시 단위 조회·검증용. create_all은 기존 테이블에 인덱스를
    # 추가하지 않으므로, 이미 존재하는 DB에도 멱등하게 적용되도록 직접 생성한다.
    with engine.begin() as conn:
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_finances_rcept_no ON finances (rcept_no)")
        )
    print(f"[DB] 초기화 완료 → {DB_PATH}")


# ── 금액 변환 유틸 ────────────────────────────────────────────────────────────

def _parse_amount(value: str | None) -> int | None:
    """
    DART API 금액 문자열을 정수로 변환한다.

    "300,870,903,000,000"  →  300870903000000
    "-11,526,297,000,000"  → -11526297000000
    "-" 또는 빈 문자열    →  None
    """
    if value is None:
        return None
    cleaned = value.replace(",", "").strip()
    if cleaned in ("-", ""):
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


# ── 저장 함수들 ───────────────────────────────────────────────────────────────

def save_disclosures(session: Session, corp_code: str, data_list: list[dict]) -> None:
    """
    공시 목록을 저장한다.
    해당 기업의 기존 데이터를 모두 지우고 새로 삽입한다 (delete → insert).
    """
    session.execute(delete(Disclosure).where(Disclosure.corp_code == corp_code))

    rows = [
        Disclosure(
            corp_code = corp_code,
            report_nm = row.get("report_nm"),
            rcept_dt  = row.get("rcept_dt"),
            rcept_no  = row.get("rcept_no"),
            flr_nm    = row.get("flr_nm"),
        )
        for row in data_list
    ]
    session.add_all(rows)
    print(f"[DB] save_disclosures → {len(rows)}건 저장")


def save_finances(session: Session, corp_code: str, data_list: list[dict]) -> None:
    """
    전체 계정과목 재무제표 데이터를 1계정 = 1행으로 저장한다.

    data_list는 get_finance_range()가 반환하는 평면(flat) 계정 행 목록이며,
    각 행은 fnlttSinglAcntAll.json 원본 필드 + fs_div + reprt_nm 을 담는다.

    같은 corp_code + bsns_year + reprt_code 조합이 있으면 기존 행(CFS·OFS 모두)을
    지우고 재삽입한다. data_list에는 OFS·CFS 행이 함께 담겨 오므로 한 번에 적재된다.
    금액·정렬순서 필드는 _parse_amount()로 쉼표를 제거해 정수로 변환한다.
    """
    # 저장 대상의 (bsns_year, reprt_code) 조합만 선택적으로 삭제 → 다른 보고서 데이터 보존
    # (해당 조합의 CFS·OFS가 함께 삭제·재삽입되어 멱등성이 유지된다)
    combos = {
        (row.get("bsns_year"), row.get("reprt_code"))
        for row in data_list
        if row.get("bsns_year") and row.get("reprt_code")
    }
    for bsns_year, reprt_code in combos:
        session.execute(
            delete(Finance).where(
                Finance.corp_code  == corp_code,
                Finance.bsns_year  == bsns_year,
                Finance.reprt_code == reprt_code,
            )
        )

    rows = [
        Finance(
            corp_code         = corp_code,
            rcept_no          = row.get("rcept_no"),
            bsns_year         = row.get("bsns_year"),
            reprt_code        = row.get("reprt_code"),
            reprt_nm          = row.get("reprt_nm"),
            fs_div            = row.get("fs_div"),
            sj_div            = row.get("sj_div"),
            sj_nm             = row.get("sj_nm"),
            account_id        = row.get("account_id"),
            account_nm        = row.get("account_nm"),
            account_detail    = row.get("account_detail"),
            ord               = _parse_amount(row.get("ord")),
            currency          = row.get("currency"),
            thstrm_nm         = row.get("thstrm_nm"),
            thstrm_amount     = _parse_amount(row.get("thstrm_amount")),
            thstrm_add_amount = _parse_amount(row.get("thstrm_add_amount")),
            frmtrm_nm         = row.get("frmtrm_nm"),
            frmtrm_amount     = _parse_amount(row.get("frmtrm_amount")),
            frmtrm_q_nm       = row.get("frmtrm_q_nm"),
            frmtrm_q_amount   = _parse_amount(row.get("frmtrm_q_amount")),
            frmtrm_add_amount = _parse_amount(row.get("frmtrm_add_amount")),
            bfefrmtrm_nm      = row.get("bfefrmtrm_nm"),
            bfefrmtrm_amount  = _parse_amount(row.get("bfefrmtrm_amount")),
        )
        for row in data_list
    ]
    session.add_all(rows)
    print(f"[DB] save_finances → {len(rows)}건 저장")


FINANCES_CSV           = DATA_DIR / "재무_사업반기.csv"
FINANCES_QUARTERLY_CSV = DATA_DIR / "재무_분기.csv"

# CSV 컬럼 순서: 식별자 → 보고서정보 → 계정정보 → 금액 (읽기 편한 순서).
# DART_FINANCE_FIELDS(원본 21개)를 빠짐없이 포함하고, 수집 측 corp_name·reprt_nm·fs_div를 더한다.
_FINANCE_FIELDNAMES = [
    # ── 식별자 ──
    "rcept_no",          # 접수번호 → DART 원문 역추적·데이터 검증 키
    "corp_code",         # DART 고유번호 → 회사 식별자(사명보다 안정적)
    "corp_name",         # 기업명 (수집 측 주입)
    # ── 보고서 정보 ──
    "bsns_year",         # 사업연도 → 연도별 정렬 기준
    "reprt_code",        # 보고서 코드
    "reprt_nm",          # 보고서명 (수집 측 주입)
    "fs_div",            # 재무제표 구분 (CFS=연결 / OFS=별도, 수집 측 주입)
    # ── 계정 정보 ──
    "sj_div",            # 재무제표 종류 (BS/IS/CIS/SCE/CF)
    "sj_nm",             # 재무제표명 (재무상태표·손익계산서 등)
    "account_id",        # XBRL 표준 계정 ID
    "account_nm",        # 계정명 (매출액·영업이익 등)
    "account_detail",    # 계정 상세
    "ord",               # 보고서 내 계정 정렬순서
    "currency",          # 통화 (KRW 등)
    # ── 금액 ──
    "thstrm_nm",         # 당기명 (제56기)
    "thstrm_amount",     # 당기금액
    "thstrm_add_amount", # 당기누적금액 (분기·반기)
    "frmtrm_nm",         # 전기명 (제55기)
    "frmtrm_amount",     # 전기금액
    "frmtrm_q_nm",       # 전기 동분기명
    "frmtrm_q_amount",   # 전기 동분기금액
    "frmtrm_add_amount", # 전기누적금액
    "bfefrmtrm_nm",      # 전전기명 (제54기)
    "bfefrmtrm_amount",  # 전전기금액 (연간 보고서)
]

# 원본 21개 필드(rcept_no·corp_code 포함)가 CSV에서 하나도 누락되지 않도록 강제한다.
# 향후 누가 필드를 빠뜨리면 import 시점에 즉시 실패시켜 회귀를 방지한다.
_missing_fields = [f for f in DART_FINANCE_FIELDS if f not in _FINANCE_FIELDNAMES]
assert not _missing_fields, f"CSV 컬럼에서 원본 필드 누락: {_missing_fields}"


def _write_finances_csv(records: list[dict], corp_name: str, csv_path: Path) -> None:
    """재무 데이터를 연도·정렬순서 오름차순으로 정렬해 CSV에 누적 저장한다."""
    if not records:
        return

    def _ord_key(r: dict) -> int:
        try:
            return int(str(r.get("ord", "")).replace(",", ""))
        except (ValueError, TypeError):
            return 0

    DATA_DIR.mkdir(exist_ok=True)
    sorted_records = sorted(
        records,
        key=lambda r: (r.get("bsns_year", ""), r.get("reprt_code", ""), r.get("sj_div", ""), _ord_key(r)),
    )
    write_header = not csv_path.exists()

    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=_FINANCE_FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in sorted_records:
            writer.writerow({"corp_name": corp_name, **row})

    print(f"[DB] CSV 저장 → {csv_path} ({len(records)}건 추가)")


def save_finances_csv(records: list[dict], corp_name: str) -> None:
    """사업보고서·반기보고서 재무 데이터를 CSV에 저장한다."""
    _write_finances_csv(records, corp_name, FINANCES_CSV)


def save_quarterly_csv(records: list[dict], corp_name: str) -> None:
    """분기보고서 재무 데이터를 CSV에 저장한다."""
    _write_finances_csv(records, corp_name, FINANCES_QUARTERLY_CSV)


def save_employees(session: Session, corp_code: str, data_list: list[dict]) -> None:
    """
    직원 현황 데이터를 저장한다 (delete → insert).
    금액 필드는 _parse_amount()로 정수 변환, sm(인원수)도 동일하게 처리한다.
    """
    session.execute(delete(Employee).where(Employee.corp_code == corp_code))

    rows = [
        Employee(
            corp_code          = corp_code,
            stlm_dt            = row.get("stlm_dt"),
            fo_bbm             = row.get("fo_bbm"),
            sexdstn            = row.get("sexdstn"),
            sm                 = _parse_amount(row.get("sm")),
            avrg_cnwk_sdytrn   = row.get("avrg_cnwk_sdytrn"),
            jan_salary_am      = _parse_amount(row.get("jan_salary_am")),
            fyer_salary_totamt = _parse_amount(row.get("fyer_salary_totamt")),
        )
        for row in data_list
    ]
    session.add_all(rows)
    print(f"[DB] save_employees → {len(rows)}건 저장")


def save_audit(session: Session, corp_code: str, data: dict) -> None:
    """
    감사보고서 수집 결과를 audit_reports 테이블에 저장한다.
    같은 corp_code + rcept_no 조합이 이미 있으면 지우고 다시 삽입한다 (delete → insert).
    """
    rcept_no = data.get("rcept_no", "")

    session.execute(
        delete(AuditReport).where(
            AuditReport.corp_code == corp_code,
            AuditReport.rcept_no  == rcept_no,
        )
    )

    row = AuditReport(
        corp_code     = corp_code,
        rcept_no      = rcept_no,
        report_nm     = data.get("report_nm", ""),
        rcept_dt      = data.get("rcept_dt", ""),
        audit_opinion = data.get("audit_opinion", "확인불가"),
        key_audit     = data.get("핵심감사사항", ""),
        created_at    = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    session.add(row)
    print(f"[DB] save_audit → {data.get('report_nm')} ({rcept_no}) 저장")


FAILURES_CSV = DATA_DIR / "수집실패.csv"

_DART_REPORT_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcept_no={rcept_no}"

_FAILURE_FIELDNAMES = [
    "collected_at",   # 수집 일시
    "corp_name",      # 회사명
    "fail_type",      # corp_code_not_found | audit_figure_missing
    "corp_code",      # DART 고유번호 (없으면 공백)
    "rcept_no",       # 접수번호 (해당 시)
    "report_nm",      # 보고서명 (해당 시)
    "missing_fields", # 파싱 실패 필드 (쉼표 구분, 예: 영업이익,당기순이익)
    "dart_link",      # DART 보고서 직링크 (audit_figure_missing일 때만 — 원문 직접 확인용)
]


def save_failures_csv(failures: list[dict]) -> None:
    """
    수집 실패 케이스를 data/수집실패.csv에 누적 저장한다.

    fail_type 종류:
      - corp_code_not_found  : 회사명이 DART corp_codes.csv에 없음 (사명 불일치)
      - audit_figure_missing : 감사보고서 재무수치(매출액·영업이익·당기순이익) 파싱 실패
    """
    if not failures:
        return

    DATA_DIR.mkdir(exist_ok=True)
    write_header = not FAILURES_CSV.exists()

    try:
        with open(FAILURES_CSV, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=_FAILURE_FIELDNAMES, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            for rec in failures:
                writer.writerow(rec)
        print(f"[DB] 수집실패 CSV → {FAILURES_CSV} ({len(failures)}건 추가)")
    except Exception as exc:
        import warnings
        warnings.warn(f"[경고] 수집실패 CSV 저장 실패: {exc}")
