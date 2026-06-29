"""reports/*.json → MariaDB company_analyses 적재.

- 테이블 자동 생성 (JSON 컬럼)
- company_id UNIQUE → 있으면 UPDATE (upsert)
- with engine.begin() + 배치커밋 → 커넥션 누수·락 점유 방지

준비:
    pip install sqlalchemy pymysql
    .env 에 MARIADB_URL=mysql+pymysql://user:pw@127.0.0.1:3306/hcr  (로컬은 SSH 터널)

실행:
    python load_company_analyses.py            # 전체 적재
    python load_company_analyses.py --dry-run  # DB 안 건드리고 개수만
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"
COMPANIES = ROOT / "hiring_preprocess/data/processed/companies_enriched_v2.jsonl"

# JSON 컬럼 (보고서 섹션 + 메타)
JSON_COLS = [
    "industry_status", "recent_trends", "financial_analysis",
    "jobplanet_review_summary", "growth_potential",
    "swot_strengths", "swot_weaknesses", "swot_opportunities", "swot_threats",
    "key_points", "source_snapshot", "sources",
]

DDL = """
CREATE TABLE IF NOT EXISTS company_analyses (
    analysis_id BIGINT PRIMARY KEY AUTO_INCREMENT,
    company_id  VARCHAR(24) NOT NULL,
    industry_status JSON, recent_trends JSON, financial_analysis JSON,
    jobplanet_review_summary JSON, growth_potential JSON,
    swot_strengths JSON, swot_weaknesses JSON, swot_opportunities JSON, swot_threats JSON,
    key_points JSON, source_snapshot JSON, sources JSON,
    analysis_version VARCHAR(10), generated_at DATE,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_company (company_id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
"""

# 기존 테이블이 다른 collation(uca1400 등)으로 만들어졌으면 통일 → news·companies와 조인되게
_FIX_COLLATION = "ALTER TABLE company_analyses CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"

_ALL = ["company_id", "analysis_version", "generated_at"] + JSON_COLS
_INSERT = (
    "INSERT INTO company_analyses (" + ", ".join(_ALL) + ") "
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


def row_params(report: dict) -> dict:
    p = {
        "company_id": report.get("company_id"),
        "analysis_version": report.get("analysis_version"),
        "generated_at": report.get("generated_at"),
    }
    for c in JSON_COLS:
        v = report.get(c)
        p[c] = json.dumps(v, ensure_ascii=False) if v is not None else None  # 없으면 NULL
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="앞 N개만 적재 (테스트용)")
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--no-stub", action="store_true",
                    help="보고서 없는 회사를 NULL 행으로 채우지 않음 (기본은 1091개 전부 행 생성)")
    args = ap.parse_args()

    files = sorted(REPORTS.glob("*.json"))
    valid = []
    for f in files:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            if r.get("company_id"):
                valid.append(r)
        except Exception as e:
            print(f"  ⚠️ 스킵 {f.name}: {e}")

    if args.limit:
        valid = valid[: args.limit]

    # 보고서 없는 회사도 company_id 행은 만든다 (분석 컬럼은 전부 NULL = 근거 없으면 비움 → 환각 0)
    stub_n = 0
    if not args.limit and not args.no_stub and COMPANIES.exists():
        have_ids = {r.get("company_id") for r in valid}
        today = datetime.date.today().isoformat()
        for line in COMPANIES.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            cid = json.loads(line).get("company_id")
            if cid and cid not in have_ids:
                # 보고서 없는 회사 stub: v2 스키마 행이되 분석 컬럼은 NULL (생성여부는 분석 컬럼 유무로 판단)
                valid.append({"company_id": cid, "analysis_version": "v2_stub", "generated_at": today})
                have_ids.add(cid)
                stub_n += 1

    print(f"보고서 {len(files)}개 / 적재 대상 {len(valid)}개"
          + (f" (보고서 {len(valid)-stub_n} + NULL행 {stub_n})" if stub_n else "")
          + (f" (--limit {args.limit})" if args.limit else ""))
    if args.dry_run:
        print("(--dry-run: DB 미접속)")
        return

    load_dotenv(ROOT / ".env")
    url = os.getenv("MARIADB_URL") or os.getenv("MARIADBURL")
    if not url:
        raise SystemExit("MARIADB_URL 없음. .env에 mysql+pymysql://user:pw@host:3306/hcr 추가.")

    from sqlalchemy import create_engine, text  # pip install sqlalchemy pymysql
    engine = create_engine(url, pool_pre_ping=True)

    # 1) 테이블 생성
    with engine.begin() as conn:
        conn.execute(text(DDL))
        # collation은 DDL(CREATE)에서 utf8mb4_unicode_ci로 지정됨. 기존 테이블이 다른 경우만 1회 변환.
        cur = conn.execute(text(
            "SELECT table_collation FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name='company_analyses'")).scalar()
        if cur and "unicode_ci" not in cur:
            conn.execute(text(_FIX_COLLATION))   # 한 번만 (매번 ALTER하면 메타데이터 락 경합)
    print("테이블 확인/생성 완료")

    # 2) 배치 적재 (락 짧게 + 중단돼도 진행분 보존)
    ok = 0
    for i in range(0, len(valid), args.batch):
        chunk = valid[i:i + args.batch]
        with engine.begin() as conn:          # 성공→commit, 예외→rollback, 끝→close 자동
            for r in chunk:
                conn.execute(text(_INSERT), row_params(r))
                ok += 1
        print(f"  ...{ok}/{len(valid)} 적재")

    engine.dispose()   # 커넥션 정리 후 깨끗하게 종료 (서버에 락/유령 커넥션 안 남게)
    print(f"\n완료: {ok}개 company_analyses 적재(upsert)")


if __name__ == "__main__":
    main()
