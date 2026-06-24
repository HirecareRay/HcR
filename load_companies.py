"""companies_enriched_v2.jsonl → MariaDB companies 마스터 테이블 적재.

이게 허브: company_id PK. news·company_analyses·dart 등이 전부 이 company_id를 FK로 참조.
collation은 DB 기본(utf8mb4_unicode_ci)으로 통일 → 다른 테이블과 조인 깨지지 않게.

준비:  pip install sqlalchemy pymysql ; .env 에 MARIADB_URL
실행:  python load_companies.py            # 전체 적재(upsert)
       python load_companies.py --dry-run  # 개수만
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).parent
COMPANIES = ROOT / "hiring_preprocess/data/processed/companies_enriched_v2.jsonl"

# company_profile 에서 가져올 컬럼 (회사 기본정보)
PROFILE_COLS = [
    "industry", "company_size", "company_type", "employee_count",
    "revenue", "founded", "ceo", "website_url", "address", "main_business",
]

DDL = """
CREATE TABLE IF NOT EXISTS companies (
    company_id     VARCHAR(24) PRIMARY KEY,
    company_name   VARCHAR(255),
    industry       VARCHAR(500),
    company_size   VARCHAR(100),
    company_type   VARCHAR(100),
    employee_count VARCHAR(255),
    revenue        VARCHAR(500),
    founded        VARCHAR(255),
    ceo            VARCHAR(255),
    website_url    VARCHAR(500),
    address        VARCHAR(500),
    main_business  TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
"""

_ALL = ["company_id", "company_name"] + PROFILE_COLS
_INSERT = (
    "INSERT INTO companies (" + ", ".join(_ALL) + ") "
    "VALUES (" + ", ".join(":" + c for c in _ALL) + ") "
    "ON DUPLICATE KEY UPDATE " +
    ", ".join(f"{c}=VALUES({c})" for c in _ALL if c != "company_id")
)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def rows() -> list[dict]:
    out = []
    for line in COMPANIES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        cid = d.get("company_id")
        if not cid:
            continue
        p = d.get("company_profile") or {}
        r = {"company_id": cid, "company_name": d.get("company_name")}
        for c in PROFILE_COLS:
            v = p.get(c)
            r[c] = str(v) if v is not None else None
        out.append(r)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=200)
    args = ap.parse_args()

    data = rows()
    print(f"companies 적재 대상: {len(data)}개")
    if args.dry_run:
        print("(--dry-run: DB 미접속)")
        return

    load_dotenv(ROOT / ".env")
    url = os.getenv("MARIADB_URL") or os.getenv("MARIADBURL")
    if not url:
        raise SystemExit("MARIADB_URL 없음. .env 확인.")

    from sqlalchemy import create_engine, text
    engine = create_engine(url, pool_pre_ping=True)

    with engine.begin() as conn:
        conn.execute(text(DDL))
    print("companies 테이블 확인/생성 완료 (collation utf8mb4_unicode_ci)")

    ok = 0
    for i in range(0, len(data), args.batch):
        chunk = data[i:i + args.batch]
        with engine.begin() as conn:
            for r in chunk:
                conn.execute(text(_INSERT), r)
                ok += 1
        print(f"  ...{ok}/{len(data)} 적재")

    print(f"\n완료: {ok}개 companies 적재(upsert)")


if __name__ == "__main__":
    main()
