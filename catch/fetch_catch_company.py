"""catch 기업개요(CompSummary) 로그인 수집 — 파트너십 접근.

공고 임베디드로는 못 가져온 소개문·초봉·평균연봉·요약재무제표가 로그인 뒤에 있어
catch/.env 의 세션 쿠키로 받아온다. 토큰은 .env 에서만 읽고 코드/깃엔 안 박는다.

  python fetch_catch_company.py --test            # 1개(111퍼센트)만, 구조 확인용 dump
  python fetch_catch_company.py                    # 222개 전체 → catch_company_detail.jsonl

의존: requests, beautifulsoup4, lxml  (dartAPI/.venv 에 있음)
"""
import argparse, json, random, sys, time
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
HCR = ROOT.parent
ENRICHED = HCR / "hiring_preprocess/data/processed/companies_enriched_v2.jsonl"
OUT = ROOT / "catch_company_detail.jsonl"


def load_cookies() -> dict:
    env = ROOT / ".env"
    if not env.exists():
        sys.exit("catch/.env 없음 — .env.example 보고 토큰 채워.")
    kv = {}
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        kv[k.strip()] = v.strip()
    missing = [k for k in ("CATCH_MEMID", "CATCH_TOKEN", "CATCH_REFRESHTOKEN") if not kv.get(k)]
    if missing:
        sys.exit(f"catch/.env 비어있음: {missing}")
    return {"memID": kv["CATCH_MEMID"], "token": kv["CATCH_TOKEN"],
            "refreshToken": kv["CATCH_REFRESHTOKEN"]}


def catch_urls() -> list[tuple[str, str]]:
    """[(company_name, compsummary_url)] — 중복 url 제거."""
    seen, out = set(), []
    for line in ENRICHED.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        for sp in (d.get("source_profiles") or []):
            u = (sp.get("company_page_url") or "").strip()
            if sp.get("source_site") == "catch" and u and u not in seen:
                seen.add(u)
                out.append((d.get("company_name"), u))
                break
    return out


HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.catch.co.kr/",
    "Connection": "keep-alive",
}


def make_session(cookies: dict) -> requests.Session:
    """세션 1개 재사용(쿠키·헤더 고정) — 매 요청 새 연결보다 봇 티 덜 남."""
    s = requests.Session()
    s.headers.update(HEADERS)
    s.cookies.update(cookies)
    return s


def fetch(session: requests.Session, url: str, retries: int = 5) -> BeautifulSoup:
    """429면 Retry-After 우선, 없으면 지수 백오프(5→10→20→40→80초)."""
    for attempt in range(retries):
        r = session.get(url, timeout=20)
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            wait = int(ra) if (ra and ra.isdigit()) else 5 * (2 ** attempt)
            print(f"    429 → {wait}s 대기 (Retry-After={ra}) {attempt + 1}/{retries}", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")
    raise RuntimeError("429 반복 — 중단")


def parse(soup: BeautifulSoup) -> dict:
    """로그인 뒤 추가 필드 추출. 선택자는 테스트 dump 보고 보정."""
    txt = soup.get_text("\n")
    lines = [l.strip() for l in txt.splitlines() if l.strip()]

    def salary_after(label):
        # 라벨 다음 몇 줄 안에서 '만원' 들어간 실제 수치 줄을 집음
        for i, l in enumerate(lines):
            if l == label:
                for l2 in lines[i + 1:i + 5]:
                    if "만원" in l2:
                        return l2
        return None

    # 소개문: "인사담당자가 소개하는" 헤더 다음 ~ 다음 섹션 경계 전까지
    BOUND = ("근무환경", "대표브랜드", "이 기업의 최신 연봉", "채용규모",
             "기업 상세정보", "요약재무제표", "이 기업과 비슷한")
    SKIP = ("기업 직접 작성",)
    intro = ""
    for i, l in enumerate(lines):
        if "인사담당자가 소개하는" in l:
            buf = []
            for l2 in lines[i + 1:i + 80]:
                if any(b in l2 for b in BOUND):
                    break
                if l2 in SKIP or l2.endswith("기준"):   # 메타 줄(작성주체·기준일) 스킵
                    continue
                buf.append(l2)
            intro = "\n".join(buf).strip()
            break
    # 대표브랜드 및 사업구성 (제품/사업 라인)
    products = []
    for i, l in enumerate(lines):
        if "대표브랜드 및 사업구성" in l:
            for l2 in lines[i + 1:i + 15]:
                if any(b in l2 for b in ("이 기업의 최신 연봉", "근무환경", "채용규모", "인사담당자")):
                    break
                products.append(l2)
            break

    # 채용규모(20XX년 채용규모 → 다음 줄) / 이직률
    hire_count = turnover = None
    for i, l in enumerate(lines):
        if "년 채용규모" in l and i + 1 < len(lines):
            hire_count = lines[i + 1]
        if l == "이직률" and i + 1 < len(lines):
            turnover = lines[i + 1]

    return {
        "intro": intro,
        "entry_salary": salary_after("초봉"),
        "avg_salary": salary_after("평균 연봉"),
        "products": products,
        "hire_count": hire_count,
        "turnover": turnover,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="1개만 받아 구조 dump")
    args = ap.parse_args()
    session = make_session(load_cookies())
    targets = catch_urls()
    print(f"catch 대상: {len(targets)}개")

    if args.test:
        name, url = next((t for t in targets if "111퍼센트" in (t[0] or "")), targets[0])
        print(f"[TEST] {name}  {url}")
        soup = fetch(session, url)
        # 로그인 확인 + 구조 확인용 raw dump
        raw = soup.get_text("\n")
        print("로그인됨?(소개 본문 단어 '재미/심플' 등장):", any(w in raw for w in ["재미", "심플", "참신"]))
        print("--- parse 결과 ---")
        print(json.dumps(parse(soup), ensure_ascii=False, indent=1)[:1500])
        return

    # 재개: 이미 받은 source_url 은 건너뛰고 append
    done = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line).get("source_url"))
    todo = [(n, u) for n, u in targets if u not in done]
    print(f"이미 받음 {len(done)} / 남음 {len(todo)}")

    n = 0
    with OUT.open("a", encoding="utf-8") as f:
        for name, url in todo:
            try:
                soup = fetch(session, url)
                d = parse(soup)
                d["raw_text"] = soup.get_text("\n")  # 재파싱용 원문 보존 → 다음부턴 재요청 불필요
                d.update({"company_name": name, "source_url": url})
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
                f.flush()                       # 한 건마다 즉시 저장 → 끊겨도 보존
                n += 1
            except Exception as e:
                print(f"  실패 {name}: {e}", flush=True)
            # 3~5초 + 0.5~2초 지터 (규칙적 간격 자체가 봇 신호)
            time.sleep(random.uniform(3.0, 5.0) + random.uniform(0.5, 2.0))
    print(f"완료: 이번 {n}건 추가 → 누적 {len(done) + n}/{len(targets)}")


if __name__ == "__main__":
    main()
