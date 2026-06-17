"""
db.py
SQLAlchemy를 사용한 SQLite DB 초기화 및 저장 함수 모음.
추후 MySQL로 교체 시 ENGINE_URL 한 줄만 변경하면 된다.
"""

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
)
from sqlalchemy.orm import DeclarativeBase, Session

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
    __tablename__ = "finances"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    corp_code        = Column(String, ForeignKey("companies.corp_code"))
    bsns_year        = Column(String)
    account_nm       = Column(String)
    fs_div           = Column(String)
    sj_div           = Column(String)
    thstrm_amount    = Column(BigInteger)
    frmtrm_amount    = Column(BigInteger)
    bfefrmtrm_amount = Column(BigInteger)


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


class Report(Base):
    __tablename__ = "reports"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    corp_code  = Column(String, ForeignKey("companies.corp_code"))
    rcept_no   = Column(String)
    section_nm = Column(String)
    content    = Column(Text)
    created_at = Column(String)


# ── DB 초기화 ─────────────────────────────────────────────────────────────────

def init_db() -> None:
    """data/ 폴더와 dart.db 파일을 만들고 테이블을 생성한다 (이미 있으면 유지)."""
    DATA_DIR.mkdir(exist_ok=True)
    Base.metadata.create_all(engine)
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

def save_company(session: Session, data: dict) -> None:
    """
    기업 기본 정보를 저장한다.
    같은 corp_code가 이미 있으면 덮어쓴다 (upsert).
    """
    obj = Company(
        corp_code   = data.get("corp_code"),
        corp_name   = data.get("corp_name"),
        ceo_nm      = data.get("ceo_nm"),
        est_dt      = data.get("est_dt"),
        induty_code = data.get("induty_code"),
        adres       = data.get("adres"),
        hm_url      = data.get("hm_url"),
        stock_code  = data.get("stock_code"),
    )
    session.merge(obj)  # PK 기준 있으면 UPDATE, 없으면 INSERT
    print(f"[DB] save_company → {obj.corp_name} ({obj.corp_code})")


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
    재무제표 데이터를 저장한다 (delete → insert).
    금액 필드는 _parse_amount()로 쉼표를 제거해 정수로 변환한다.
    """
    session.execute(delete(Finance).where(Finance.corp_code == corp_code))

    rows = [
        Finance(
            corp_code        = corp_code,
            bsns_year        = row.get("bsns_year"),
            account_nm       = row.get("account_nm"),
            fs_div           = row.get("fs_div"),
            sj_div           = row.get("sj_div"),
            thstrm_amount    = _parse_amount(row.get("thstrm_amount")),
            frmtrm_amount    = _parse_amount(row.get("frmtrm_amount")),
            bfefrmtrm_amount = _parse_amount(row.get("bfefrmtrm_amount")),
        )
        for row in data_list
    ]
    session.add_all(rows)
    print(f"[DB] save_finances → {len(rows)}건 저장")


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


def save_report_text(
    session: Session,
    corp_code: str,
    rcept_no: str,
    section_nm: str,
    content: str,
) -> None:
    """
    보고서 원문 텍스트를 저장한다.
    같은 corp_code + rcept_no + section_nm 조합이 있으면 지우고 다시 삽입한다.
    """
    session.execute(
        delete(Report).where(
            Report.corp_code  == corp_code,
            Report.rcept_no   == rcept_no,
            Report.section_nm == section_nm,
        )
    )

    row = Report(
        corp_code  = corp_code,
        rcept_no   = rcept_no,
        section_nm = section_nm,
        content    = content,
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    session.add(row)
    print(f"[DB] save_report_text → '{section_nm}' {len(content)}자 저장")
