"""catch summary API 수집 — 222개 전체. DOM 대체.

GET /api/v1.0/comp/compSummary/summary/{code}?isForceOpen=false 한 방으로
소개(oneWord.CompCmt)·brandList·salaryList·recruitSize(이직률 포함)·aiCompSummary
까지 JSON으로 받는다. raw 를 catch_summary_raw.jsonl 에 한 줄씩 저장(재파싱용).

  python fetch_catch_summary.py --test     # 1개만
  python fetch_catch_summary.py            # 222개 (재개·flush)
"""
import argparse, json, random, time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
ENRICHED = ROOT.parent / "hiring_preprocess/data/processed/companies_enriched_v2.jsonl"
RAW = ROOT / "catch_summary_raw.jsonl"
BASE = "https://www.catch.co.kr"
SUMMARY = "/api/v1.0/comp/compSummary/summary/{code}?isForceOpen=false"


def load_cookies() -> dict:
    kv = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    return {"memID": kv["CATCH_MEMID"], "token": kv["CATCH_TOKEN"],
            "refreshToken": kv["CATCH_REFRESHTOKEN"]}


def targets() -> list[tuple[str, str]]:
    """[(company_name, code)] — code = CompSummary URL 끝 세그먼트. 중복 제거."""
    seen, out = set(), []
    for line in ENRICHED.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        for sp in (d.get("source_profiles") or []):
            u = (sp.get("company_page_url") or "").strip()
            if sp.get("source_site") == "catch" and u:
                code = u.rstrip("/").split("/")[-1]
                if code and code not in seen:
                    seen.add(code)
                    out.append((d.get("company_name"), code))
                break
    return out


def make_session(cookies: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
    })
    s.cookies.update(cookies)
    return s


def fetch_summary(s: requests.Session, code: str, retries: int = 5):
    url = BASE + SUMMARY.format(code=code)
    ref = f"{BASE}/Comp/CompSummary/{code}"
    for attempt in range(retries):
        r = s.get(url, headers={"Referer": ref}, timeout=20)
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            wait = int(ra) if (ra and ra.isdigit()) else 5 * (2 ** attempt)
            print(f"    429 → {wait}s 대기 {attempt+1}/{retries}", flush=True)
            time.sleep(wait)
            continue
        return r.status_code, (r.json() if r.status_code == 200 and r.text.strip() else None)
    return 429, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()
    s = make_session(load_cookies())
    tg = targets()
    print(f"대상: {len(tg)}개")

    if args.test:
        name, code = tg[0]
        st, js = fetch_summary(s, code)
        print(f"[TEST] {name} {code} status={st} keys={list(js.keys()) if js else None}")
        return

    done = set()
    if RAW.exists():
        for line in RAW.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line).get("code"))
    todo = [(n, c) for n, c in tg if c not in done]
    print(f"이미 받음 {len(done)} / 남음 {len(todo)}")

    ok = fail = 0
    with RAW.open("a", encoding="utf-8") as f:
        for name, code in todo:
            try:
                st, js = fetch_summary(s, code)
                rec = {"code": code, "company_name": name, "status": st,
                       "ok": st == 200 and js is not None, "summary": js}
                if not rec["ok"]:
                    fail += 1
                    print(f"  실패 {name}({code}): status={st}", flush=True)
                else:
                    ok += 1
            except Exception as e:
                rec = {"code": code, "company_name": name, "status": None, "ok": False, "error": str(e)}
                fail += 1
                print(f"  에러 {name}({code}): {e}", flush=True)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            time.sleep(random.uniform(3.0, 5.0) + random.uniform(0.5, 2.0))
    print(f"완료: 성공 {ok} 실패 {fail} / 누적 {len(done)+ok+fail}")


if __name__ == "__main__":
    main()
