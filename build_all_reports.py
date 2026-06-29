"""전체 회사 보고서 배치 생성 — build_company_report 를 모든 회사에 돌린다.

- 회사별 JSON → reports/{company_id}.json
- 이미 만든 건 건너뜀 (중단 후 재개 가능)
- 한 회사 실패해도 다음으로 (격리)
- --workers N 으로 OpenAI 호출 병렬화 (I/O 대기라 스레드로 N배 빨라짐)

실행 (HcR 기준):
    python build_all_reports.py --limit 5            # 소액 테스트 (앞 5개)
    python build_all_reports.py                      # 전체 (순차)
    python build_all_reports.py --workers 6          # 6개 동시 (빠름)
    python build_all_reports.py --refresh-financial --workers 6  # 재무회사 갱신 + 나머지 생성
    python build_all_reports.py --only "CJ ENM"      # 특정 회사만
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import build_company_report as rep

ROOT = Path(__file__).parent
COMPANIES = ROOT / "hiring_preprocess/data/processed/companies_enriched_v2.jsonl"
OUT_DIR = ROOT / "reports"


def company_list() -> list[dict]:
    out = []
    for line in COMPANIES.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out.append({"company_id": d["company_id"], "company_name": d.get("company_name", "")})
    return out


def _process_one(c: dict, args) -> tuple[str, str, str]:
    """회사 1개 처리 → ('ok'|'skip'|'fail', 회사명, 메시지). 스레드에서 호출됨."""
    out = OUT_DIR / f"{c['company_id']}.json"
    name = c["company_name"]
    exists = out.exists()
    try:
        data = rep.gather(name)
        # 분석 데이터 없으면 스킵 (뉴스·재무·잡플·사업설명·인재상 다 없으면 = 업종·규모뿐 → 환각 위험)
        if not (data.get("news") or data.get("financial_indicators")
                or data.get("financial_audit") or data.get("financial_interim")
                or data.get("jobplanet") or data.get("business_description")
                or data.get("talent_values") or data.get("main_products_services")):
            return ("skip", name, "")
        # refresh-financial: 재무 데이터 있는 회사면 (DART 확장 반영 위해) 재생성, 없으면 건너뜀
        if exists and not args.overwrite and args.refresh_financial:
            has_fin = (data.get("financial_indicators") or data.get("financial_audit")
                       or data.get("financial_interim"))
            if not has_fin:
                return ("skip", name, "")
        report = rep.call_openai(rep.build_prompt(data), rep.build_schema(data))
        report = rep.finalize_report(report, data, name)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return ("ok", name, "")
    except Exception as e:
        return ("fail", name, str(e)[:80])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞 N개만 (소액 테스트)")
    ap.add_argument("--only", type=str, default=None, help="특정 회사명만")
    ap.add_argument("--ids", type=str, default=None,
                    help="특정 company_id만 (콤마구분). 지정 회사만 재생성 (--overwrite와 함께)")
    ap.add_argument("--overwrite", action="store_true", help="이미 만든 것도 다시 생성")
    ap.add_argument("--refresh-financial", action="store_true",
                    help="이미 만든 것 중 재무 데이터(재무지표/감사/분기반기) 있는 회사는 모두 재생성 (DART 확장 반영)")
    ap.add_argument("--workers", type=int, default=1,
                    help="동시 OpenAI 호출 수 (병렬). 기본 1=순차, 6 권장")
    args = ap.parse_args()

    rep.load_dotenv_file(ROOT / ".env")
    import os
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY 없음. .env 확인.")

    OUT_DIR.mkdir(exist_ok=True)
    companies = company_list()
    if args.only:
        companies = [c for c in companies if c["company_name"] == args.only]
    if args.ids:
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        companies = [c for c in companies if c["company_id"] in want]
    if args.limit:
        companies = companies[: args.limit]

    # 이미 있고 overwrite/refresh 둘 다 아니면 미리 제외 (스레드도 안 띄움 = 빠른 재개)
    if not args.overwrite and not args.refresh_financial:
        pending = [c for c in companies if not (OUT_DIR / f"{c['company_id']}.json").exists()]
    else:
        pending = companies
    skip_pre = len(companies) - len(pending)
    total = len(companies)
    print(f"대상 {total}개 / 처리 {len(pending)}개 (이미완료 {skip_pre}) / 동시 {args.workers}개", flush=True)

    # 스레드 경쟁 방지: 공유 캐시(인덱스·DART·뉴스 등)를 메인 스레드에서 미리 빌드
    rep._index(); rep._jobplanet_by_cid(); rep._dart_by_cid()
    rep._audit_by_cid(); rep._interim_by_cid(); rep._employee_by_cid()
    rep._history_by_cid(); rep._news_by_cid()   # _news_by_cid = DB 벌크로드(터널 필요)

    ok = skip = fail = done = 0
    n = len(pending)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(_process_one, c, args): c for c in pending}
        for fut in as_completed(futs):
            status, name, msg = fut.result()
            done += 1
            if status == "ok":
                ok += 1
                print(f"  [{done}/{n}] ✅ {name}", flush=True)
            elif status == "fail":
                fail += 1
                print(f"  [{done}/{n}] ❌ {name} — {msg}", flush=True)
            else:
                skip += 1

    print(f"\n완료: 생성 {ok} / 건너뜀 {skip + skip_pre} / 실패 {fail}", flush=True)
    print(f"→ {OUT_DIR}/ 에 회사별 보고서 JSON (company_analyses 적재용)", flush=True)


if __name__ == "__main__":
    main()
