"""catch 내부 API 직접 호출 수집 (DOM/Playwright 대체).

기업개요 페이지가 부르는 내부 REST API를 세션 쿠키로 직접 호출한다. JS 렌더링
불필요 + 재무상세(financeDetailV2)까지 JSON으로 받는다.

  python fetch_catch_api.py IV6821      # 단건 테스트: raw dump + 구조 출력
  python fetch_catch_api.py             # (통합 후) 전체

각 엔드포인트 응답을 raw 로 catch_{code}_api_dump.json 에 저장. 403/404/빈응답은
실패로 기록하고 계속 진행. readCnt 는 optional(404 가능).
"""
import json, sys
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
BASE = "https://www.catch.co.kr"


def load_cookies() -> dict:
    env = ROOT / ".env"
    kv = {}
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    return {"memID": kv["CATCH_MEMID"], "token": kv["CATCH_TOKEN"],
            "refreshToken": kv["CATCH_REFRESHTOKEN"]}


def endpoints(code: str) -> dict:
    return {
        "info":          f"/api/v1.0/comp/compInfo/info/{code}",
        "financeDetailV2": f"/api/v1.0/comp/compInfo/financeDetailV2/{code}",
        "summary":       f"/api/v1.0/comp/compSummary/summary/{code}?isForceOpen=false",
        "faq":           f"/api/v1.0/comp/recruitInfo/faq/{code}",
        "sameRecruit":   f"/api/v1.0/comp/compSummary/getSameJinhakCodeRecruit/{code}",
        "dongJongHit":   f"/api/v1.0/comp/compSummary/dongJongHit/{code}",
        "groupInfo":     f"/api/v1.0/comp/compInfo/groupInfo/{code}/0/500",
        "recomCategory": "/api/v1.0/comp/compSummary/recomCategory",
        "readCnt":       f"/api/v1.0/comp/compSummary/{code}/readCnt",   # optional
    }

OPTIONAL = {"readCnt"}


def session(cookies: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
    })
    s.cookies.update(cookies)
    return s


def fetch_all(s: requests.Session, code: str) -> dict:
    """엔드포인트별 {status, ok, json|text|error}. 실패해도 계속."""
    ref = f"{BASE}/Comp/CompSummary/{code}"
    out = {}
    for name, path in endpoints(code).items():
        rec = {"url": BASE + path}
        try:
            r = s.get(BASE + path, headers={"Referer": ref}, timeout=20)
            rec["status"] = r.status_code
            rec["ok"] = r.status_code == 200 and bool(r.text.strip())
            if r.status_code == 200 and r.text.strip():
                try:
                    rec["json"] = r.json()
                except ValueError:
                    rec["text"] = r.text[:2000]
            else:
                rec["fail_reason"] = f"status={r.status_code} len={len(r.text)}"
        except Exception as e:
            rec["status"] = None
            rec["ok"] = False
            rec["error"] = str(e)
        if not rec["ok"] and name in OPTIONAL:
            rec["optional"] = True
        out[name] = rec
    return out


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "IV6821"
    s = session(load_cookies())
    dump = fetch_all(s, code)
    out = ROOT / f"catch_{code}_api_dump.json"
    out.write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"raw dump 저장: {out}\n")
    for name, rec in dump.items():
        tag = "OK" if rec["ok"] else ("optional-skip" if rec.get("optional") else "FAIL")
        keys = list(rec["json"].keys()) if isinstance(rec.get("json"), dict) else ("(text)" if "text" in rec else "")
        print(f"[{tag:13}] {name:15} status={rec.get('status')}  keys={keys}")


if __name__ == "__main__":
    main()
