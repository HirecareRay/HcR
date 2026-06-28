"""기업 분석 보고서 생성 — 모든 소스 합쳐서 company_analyses 전체 생성.

뉴스(RAG) + DART 재무 + 인재상 + 경쟁사 + 기본정보 → 한 번의 LLM 호출 → 전체 보고서.

실행 (HcR 기준):
    python build_company_report.py "CJ ENM"

.env 의 OPENAI_API_KEY 자동 로드.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import time
from pathlib import Path

import requests

import company_resolver as cr

ROOT = Path(__file__).parent
NEWS_DIR = ROOT / "news_preprocess/article"
COMPANIES = ROOT / "hiring_preprocess/data/processed/companies_enriched_v2.jsonl"
SIMILAR = ROOT / "hiring_preprocess/data/processed/similar_companies.jsonl"
CRAWLER = ROOT / "company-crawler/data/company-crawler.json"
DART_INDICATORS = ROOT / "dartAPI/data/재무지표.json"
DART_AUDIT = ROOT / "dartAPI/data/감사보고서.json"   # 실제 매출·이익·자산 + 감사의견 (174개 회사)
DART_QUARTER = ROOT / "dartAPI/data/재무_분기.json"   # 분기 재무제표
DART_HALF = ROOT / "dartAPI/data/재무_사업반기.json"  # 반기/사업 재무제표
DART_EMPLOYEE = ROOT / "dartAPI/data/직원현황.json"   # 부서별 인원·급여·근속
JOBPLANET_DIR = ROOT / "scrapy/jobplanet_review"
JOBKOREA_PAGES = ROOT / "hiring_preprocess/data/processed/company_pages_jobkorea_v1.jsonl"  # 연혁(history) 출처

OPENAI_URL = "https://api.openai.com/v1/responses"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

# 근거(evidence) 포함 섹션 — summary(서술) + evidence(근거로 쓴 실제 데이터)
def _sec() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},  # 어떤 뉴스/숫자/리뷰를 봤나
        },
        "required": ["summary", "evidence"],
    }


# 보고서 스키마 — 회사별 동적 생성. 데이터 없는 섹션(재무·잡플래닛)은 아예 제외(넘김).
def build_schema(data: dict) -> dict:
    props = {
        "growth_potential": _sec(),
        "swot_strengths": {"type": "array", "items": {"type": "string"}},
        "swot_weaknesses": {"type": "array", "items": {"type": "string"}},
        "swot_opportunities": {"type": "array", "items": {"type": "string"}},
        "swot_threats": {"type": "array", "items": {"type": "string"}},
        "key_points": {"type": "array", "items": {"type": "string"}},
    }
    if data.get("news"):                   # 뉴스 있을 때만 (없으면 업계·동향 섹션 제외 → 비워둠)
        props["industry_status"] = _sec()
        props["recent_trends"] = _sec()
    if data.get("financial_indicators") or data.get("financial_audit") or data.get("financial_interim"):
        props["financial_analysis"] = _sec()   # DART 재무지표/감사보고서/분기·반기 중 하나라도 있으면
    if data.get("jobplanet"):              # 잡플래닛 리뷰 있을 때만
        props["jobplanet_review_summary"] = _sec()
    return {"type": "object", "additionalProperties": False,
            "properties": props, "required": list(props.keys())}

SYSTEM_PROMPT = (
    "너는 취업준비생을 위한 기업 분석가다. 반드시 주어진 실제 데이터만 근거로 분석한다.\n"
    "- 데이터에 없는 내용은 절대 추론하거나 지어내지 마라.\n"
    "- 제공된 섹션만 작성한다. 데이터 없는 섹션은 스키마에 없으니 신경 쓰지 마라.\n"
    "- evidence의 각 항목은 '검증 가능한 사실 주장 문장'으로 쓴다. 기사 제목을 그대로 옮기지 말고, 그 근거가 뒷받침하는 구체적 사실(숫자·사건·결과)을 완결된 한 문장으로 진술한다.\n"
    "  (나쁜 예: '한국경제: 업스테이지 유니콘 등극 기념식' / 좋은 예: '유니콘 등극과 5600억원 투자 유치로 시장 신뢰를 확보했다 [3]')\n"
    "- summary는 섹션 전체를 서술하고, evidence는 그 서술을 떠받치는 개별 팩트들이다 (서로 다른 역할).\n"
    "- 뉴스 기반 evidence·SWOT·key_points 항목 끝에는 그 뉴스의 번호를 [n]으로 단다 (출처 키는 후처리로 붙음).\n"
    "- 하나의 evidence 항목에는 '하나의 핵심 사실'만 담는다 (여러 사실을 한 문장에 욱여넣지 마라).\n"
    "- URL·출처키를 절대 직접 생성하지 마라. 출처 표시는 오직 제공된 [n] 번호로만 한다.\n"
    "- 회사 프로필 기본정보(직원수·매출·설립연도·업종 등)에서 가져온 사실은 evidence 끝에 [P]를 단다 (뉴스는 [n], 둘 다면 함께).\n"
    "- 출처(뉴스)가 없는 분석적 해석은 [n]을 달지 않는다 (후처리에서 evidence_type='inference'로 분류됨). 없는 출처를 지어내지 마라.\n"
    "- 기본정보(회사 프로필)의 매출·직원수도 유효한 데이터다. DART 상세 재무가 없을 뿐이며, 매출 등이 있으면 '재무 데이터 부재'라고 단정하지 말고 '상세 재무공시 없음'으로 표현한다.\n"
    "- industry_status는 회사가 속한 산업의 구조적 상황·시장 경쟁·수요 변화 등 외부 환경을 요약한다.\n"
    "- recent_trends는 최근 뉴스에 나온 회사 관련 사건·사업 변화·리스크를 요약한다 (뉴스 없으면 추정 금지).\n"
    "- financial_analysis는 DART 재무지표/감사보고서/분기·반기 숫자(매출·영업이익·순이익·자산·부채 등)가 있을 때만 해석해 작성한다. 금액 단위는 원이며 억·조로 환산해 설명하고, 감사의견과 최근 분기 실적 추세, 직원 규모·평균연봉도 있으면 언급한다. 모두 없으면 '재무 데이터 없음'.\n"
    "- jobplanet_review_summary는 pros/cons를 균형 있게 요약한다. 없으면 '리뷰 데이터 없음'.\n"
    "- growth_potential은 기본정보·사업설명·뉴스·재무·연혁·직원규모를 종합하되 근거 없는 낙관론을 피한다.\n"
    "- 연혁(history)은 출력 섹션으로 만들지 말고 '분석 근거'로만 활용한다 (성장 궤적·주요 이정표·해외진출 흐름 파악).\n"
    "- 해외진출·글로벌 사업이 연혁/뉴스/사업설명에 나타나면 growth_potential·industry_status에서 언급한다 (없으면 지어내지 마라).\n"
    "- SWOT은 강점·약점·기회·위협을 각각 2~3개 작성한다. 기회/위협은 외부 환경과 뉴스에서 도출한다. 뉴스 근거 항목은 끝에 [n] 인용을 단다.\n"
    "- 모든 문장은 취준생이 자기소개서·면접 답변에 활용할 수 있게 구체적으로 쓴다.\n"
    "- 문체는 '~이다/~한다' 평서형 종결로 통일한다. '~습니다/~합니다' 체를 섞지 마라.\n"
    "- 재무 수치는 반드시 연결/별도 기준을 명시한다. 원칙적으로 연결(CFS) 기준을 우선 사용하고, 별도 수치는 '별도 기준'이라 명시한 경우에만 보조로 쓴다. "
    "같은 지표에 두 기준 값이 다르면 둘 다 기준을 붙여 함께 제시한다 (예: '2026년 1분기 연결 영업이익은 15억원, 별도 영업이익은 195억원이다'). 기준 없이 숫자만 적어 모순을 만들지 마라.\n"
    "- 회사 고유 정보(뉴스·재무·리뷰·프로필)가 빈약한 회사는 산업 일반론으로 분량을 억지로 채우지 마라. "
    "확인된 사실만 짧게 쓰고, summary에 '공개 정보가 제한적이어서 분석에 한계가 있다'는 점을 명시한다.\n"
)


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def norm(s: str) -> str:
    return re.sub(r"[\s()㈜주식회사]", "", s or "").lower()


# ---- resolver 기반 캐시 (한 번만 빌드: 소스 이름 → company_id 안전 매칭) ----
_INDEX = None
_JP_BY_CID = None      # company_id → 잡플래닛 파일명
_DART_BY_CID = None    # company_id → DART indicators


def _index() -> dict:
    global _INDEX
    if _INDEX is None:
        _INDEX = cr.load_company_index(COMPANIES)
    return _INDEX


def _jobplanet_by_cid() -> dict:
    global _JP_BY_CID
    if _JP_BY_CID is None:
        _JP_BY_CID = {}
        if JOBPLANET_DIR.exists():
            for f in os.listdir(JOBPLANET_DIR):
                if f.endswith("_jobplanet.jsonl"):
                    name = re.sub(r"_\d+_jobplanet\.jsonl$", "", f)
                    cid = cr.resolve(name, _index())  # exact/normalize/safe_alias only
                    if cid:
                        _JP_BY_CID.setdefault(cid, f)
    return _JP_BY_CID


def _dart_by_cid() -> dict:
    global _DART_BY_CID
    if _DART_BY_CID is None:
        _DART_BY_CID = {}
        if DART_INDICATORS.exists():
            for r in json.loads(DART_INDICATORS.read_text(encoding="utf-8")):
                cid = cr.resolve(r.get("corp_name", ""), _index())
                if cid:
                    _DART_BY_CID.setdefault(cid, r.get("indicators"))
    return _DART_BY_CID


_AUDIT_BY_CID = None   # company_id → 감사보고서 핵심 재무(매출·이익·자산 + 감사의견)


def _audit_by_cid() -> dict:
    """감사보고서.json → company_id별 '가장 최근' 재무 1건.

    한 회사가 연도별로 여러 건 → rcept_dt(접수일)가 가장 큰(최신) 1건만 고른다.
    financial_tables(원본 표 전체)는 너무 커서 버리고, 분석에 쓸 핵심 숫자만 추린다.
    재무지표(54개)보다 커버리지가 넓다(174개).
    """
    global _AUDIT_BY_CID
    if _AUDIT_BY_CID is None:
        _AUDIT_BY_CID = {}
        if DART_AUDIT.exists():
            best: dict = {}   # cid → (rcept_dt, record) : 회사별 최신 1건만 남김
            for r in json.loads(DART_AUDIT.read_text(encoding="utf-8")):
                cid = cr.resolve(r.get("corp_name", ""), _index())   # 안전 매칭만
                if not cid:
                    continue
                dt = r.get("rcept_dt") or ""
                if cid not in best or dt > best[cid][0]:   # 더 최근 접수일이면 교체
                    best[cid] = (dt, r)
            keep = ("report_nm", "rcept_dt", "audit_opinion", "key_audit_matter",
                    "revenue", "operating_income", "net_income",
                    "total_assets", "total_liabilities", "total_equity",
                    "operating_cash_flow", "investing_cash_flow", "financing_cash_flow")
            for cid, (_, r) in best.items():
                _AUDIT_BY_CID[cid] = {k: r.get(k) for k in keep if r.get(k) is not None}
    return _AUDIT_BY_CID


def _line_item(statements: dict, table: str, *names) -> float | None:
    """재무제표 표(table)에서 라인아이템의 'current'(당기) 값 1개 뽑기.

    names를 순서대로 시도: 먼저 정확히 일치, 없으면 부분일치(예: '분기순손실'을 '순이익/순손실'로).
    분기/반기 statements는 {표: {항목: {current, prior}}} 구조라 current만 본다.
    """
    t = statements.get(table, {})
    if not isinstance(t, dict):
        return None
    for n in names:                                  # 1) 정확히 일치
        v = t.get(n)
        if isinstance(v, dict) and v.get("current") is not None:
            return v["current"]
    for n in names:                                  # 2) 부분 일치 (이름 표기 흔들림 대응)
        for k, v in t.items():
            if n in k and isinstance(v, dict) and v.get("current") is not None:
                return v["current"]
    return None


_INTERIM_BY_CID = None   # company_id → 최신 분기/반기 헤드라인 재무


def _interim_by_cid() -> dict:
    """재무_분기.json + 재무_사업반기.json → company_id별 '가장 최근' 분기/반기 헤드라인.

    표 4개·라인아이템 수백 개를 통째로 쓰면 토큰·저장 둘 다 과해서,
    매출·영업이익·순이익·자산/부채/자본 6개 숫자만 발췌한다.
    분기·반기를 합쳐 rcept_no(접수번호, 클수록 최신)가 가장 큰 1건 선택.
    """
    global _INTERIM_BY_CID
    if _INTERIM_BY_CID is None:
        _INTERIM_BY_CID = {}
        best: dict = {}   # cid → (rcept_no, record)
        for path in (DART_QUARTER, DART_HALF):
            if not path.exists():
                continue
            for r in json.loads(path.read_text(encoding="utf-8")):
                cid = cr.resolve(r.get("corp_name", ""), _index())
                if not cid:
                    continue
                rno = r.get("rcept_no") or ""
                if cid not in best or rno > best[cid][0]:   # 더 최근 접수번호면 교체
                    best[cid] = (rno, r)
        for cid, (_, r) in best.items():
            st = r.get("statements", {})
            head = {
                "기간": (r.get("periods") or {}).get("current"),
                "보고서": r.get("reprt_nm"),
                "기준": "연결" if r.get("fs_div") == "CFS" else "별도",   # 연결/별도 명시 (숫자 혼동 방지)
                "매출액": _line_item(st, "포괄손익계산서", "매출액", "매출"),
                "영업이익": _line_item(st, "포괄손익계산서", "영업이익(손실)", "영업이익"),
                "순이익": _line_item(st, "포괄손익계산서", "당기순이익", "반기순이익",
                                   "분기순이익", "당기순손실", "반기순손실", "분기순손실"),
                "자산총계": _line_item(st, "재무상태표", "자산총계"),
                "부채총계": _line_item(st, "재무상태표", "부채총계"),
                "자본총계": _line_item(st, "재무상태표", "자본총계"),
            }
            _INTERIM_BY_CID[cid] = {k: v for k, v in head.items() if v is not None}
    return _INTERIM_BY_CID


_EMPLOYEE_BY_CID = None   # company_id → 직원 총원·평균연봉·평균근속(집계)


def _employee_by_cid() -> dict:
    """직원현황.json → company_id별 '가장 최근' 연도의 인원·급여·근속 집계.

    원본은 부서×성별로 쪼개져 있어, 회사 단위로 합산한다:
      total_employees = 모든 부서·성별 head_count 합
      avg_salary      = (총급여 합) / (급여 집계된 인원)  ← 가중평균
      avg_tenure      = 값이 있는 항목들의 단순평균
    """
    global _EMPLOYEE_BY_CID
    if _EMPLOYEE_BY_CID is None:
        _EMPLOYEE_BY_CID = {}
        if DART_EMPLOYEE.exists():
            best: dict = {}   # cid → (bsns_year, record)
            for r in json.loads(DART_EMPLOYEE.read_text(encoding="utf-8")):
                cid = cr.resolve(r.get("corp_name", ""), _index())
                if not cid:
                    continue
                yr = str(r.get("bsns_year") or "")
                if cid not in best or yr > best[cid][0]:
                    best[cid] = (yr, r)
            for cid, (_, r) in best.items():
                total_hc = total_sal = sal_hc = 0
                tenures = []
                for genders in (r.get("divisions") or {}).values():
                    if not isinstance(genders, dict):
                        continue
                    for info in genders.values():
                        if not isinstance(info, dict):
                            continue
                        hc = info.get("head_count") or 0
                        total_hc += hc
                        ts = info.get("total_salary")
                        if ts:
                            total_sal += ts
                            sal_hc += hc
                        tn = info.get("avg_tenure")
                        if tn:
                            tenures.append(tn)
                out = {"bsns_year": r.get("bsns_year"), "total_employees": total_hc or None}
                if sal_hc:
                    out["avg_salary"] = round(total_sal / sal_hc)
                if tenures:
                    out["avg_tenure"] = round(sum(tenures) / len(tenures), 1)
                _EMPLOYEE_BY_CID[cid] = {k: v for k, v in out.items() if v is not None}
    return _EMPLOYEE_BY_CID


_HISTORY_BY_CID = None   # company_id → 연혁(최근 항목 위주) — 잡코리아 회사페이지


def _history_by_cid() -> dict:
    """잡코리아 회사페이지 → company_id별 연혁(history).

    company_ids(우리 company_id)로 이미 연결돼 있어 매칭 불필요.
    구조: [{year, month, events:[...]}] (최신순). 토큰 절약 위해 최근 12개 이벤트만.
    year가 빈 항목은 직전 year를 이어받는다(원본이 같은 해를 묶어 표기).
    분석 '근거'로만 쓰고 출력 섹션으로는 안 만든다 (잡코리아 148개사만 보유).
    """
    global _HISTORY_BY_CID
    if _HISTORY_BY_CID is None:
        _HISTORY_BY_CID = {}
        if JOBKOREA_PAGES.exists():
            for line in JOBKOREA_PAGES.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                hist = (d.get("profile") or {}).get("history")
                if not hist:
                    continue
                flat = []
                last_year = ""
                for h in hist:
                    y = h.get("year") or last_year
                    last_year = y or last_year
                    m = h.get("month") or ""
                    when = (f"{y}.{m}" if (y and m) else (y or "")).strip(".")
                    for ev in (h.get("events") or []):
                        flat.append(f"{when}: {ev}" if when else ev)
                flat = flat[:12]
                if flat:
                    for cid in (d.get("company_ids") or []):
                        if cid:
                            _HISTORY_BY_CID.setdefault(cid, flat)
    return _HISTORY_BY_CID


_NEWS_BY_CID = None


def _news_by_cid() -> dict:
    """MariaDB news 테이블 → company_id별 최근 뉴스 (실제 언론사 url + media + key).

    news.company(회사명/코드)를 resolver로 company_id에 안전매칭.
    한 번에 벌크 로드 후 메모리 그룹핑 → 병렬 생성 시 워커마다 DB 안 침 (시작 때 1회).
    각 뉴스: {key=news.id, date, title, media, url, body}. (로컬 naver 파일 폐기)
    ⚠️ MARIADB_URL + SSH 터널(3306) 필요.
    """
    global _NEWS_BY_CID
    if _NEWS_BY_CID is None:
        _NEWS_BY_CID = {}
        url = os.getenv("MARIADB_URL") or os.getenv("MARIADBURL")
        if not url:
            raise SystemExit("MARIADB_URL 없음 — 뉴스를 news 테이블에서 읽으려면 .env 필요 (터널도 켜둘 것).")
        from sqlalchemy import create_engine, text
        eng = create_engine(url, pool_pre_ping=True)
        idx = _index()
        with eng.connect() as conn:
            # news.company → company_id (안전매칭만)
            comp2cid = {}
            for (comp,) in conn.execute(text("SELECT DISTINCT company FROM news")):
                cid = cr.resolve(comp, idx)
                if cid:
                    comp2cid[comp] = cid
            # 벌크 로드 후 company_id별 그룹핑
            buckets: dict = {}
            res = conn.execute(text(
                "SELECT company, id, title, media, url, `date`, LEFT(page_content, 600) FROM news"))
            for comp, nid, title, media, nurl, ndate, body in res:
                cid = comp2cid.get(comp)
                if not cid:
                    continue
                buckets.setdefault(cid, []).append({
                    "key": nid, "date": str(ndate) if ndate else "",
                    "title": title, "media": media, "url": nurl,
                    "body": (body or "").replace("passage: ", "").strip()[:400],
                })
        for cid, items in buckets.items():
            items.sort(key=lambda r: r.get("date") or "", reverse=True)
            _NEWS_BY_CID[cid] = items
        eng.dispose()   # 벌크로드 끝 → 커넥션 정리 (프로세스 hang 방지)
    return _NEWS_BY_CID


def gather(company: str) -> dict:
    """회사명으로 모든 소스에서 데이터 수집."""
    data: dict = {"company_name": company}
    cid = cr.resolve(company, _index())
    data["company_id"] = cid
    key = norm(company)

    # 1) 기본정보 (companies_enriched)
    for line in COMPANIES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("company_id") == cid or norm(d.get("company_name", "")) == key:
            p = d.get("company_profile", {})
            data["basic"] = {k: p.get(k) for k in
                             ("industry", "company_size", "company_type", "employee_count",
                              "revenue", "founded", "main_business")}
            data["has_recruit"] = bool(d.get("posting_refs"))   # 채용공고 보유 여부
            data["company_id"] = d.get("company_id")
            cid = d.get("company_id")
            break

    # 2) 인재상 + 사업설명 (company-crawler)
    for r in json.loads(CRAWLER.read_text(encoding="utf-8")):
        if norm(r.get("company_name", "")) == key:
            data["talent_values"] = r.get("talent_values")
            data["business_description"] = r.get("business_description")
            data["main_products_services"] = r.get("main_products_services")
            break

    # 3) DART 재무지표 (resolver로 안전 매칭)
    if cid and _dart_by_cid().get(cid):
        data["financial_indicators"] = _dart_by_cid()[cid]

    # 3.1) DART 감사보고서 재무 (실제 매출·이익·자산 + 감사의견 — 재무지표보다 회사 커버리지 넓음)
    if cid and _audit_by_cid().get(cid):
        data["financial_audit"] = _audit_by_cid()[cid]

    # 3.2) DART 분기/반기 헤드라인 (최신 분기 실적 — 매출·이익·자산 6개 숫자만)
    if cid and _interim_by_cid().get(cid):
        data["financial_interim"] = _interim_by_cid()[cid]

    # 3.3) DART 직원현황 (총원·평균연봉·평균근속 — 회사 규모/처우 파악용)
    if cid and _employee_by_cid().get(cid):
        data["employees"] = _employee_by_cid()[cid]

    # 3.4) 연혁 (잡코리아) — 분석 '근거'로만 사용 (성장궤적·해외진출 도출). 출력 섹션 아님.
    if cid and _history_by_cid().get(cid):
        data["history"] = _history_by_cid()[cid]

    # 3.5) 경쟁사 (similar_companies → 이름 목록)
    if SIMILAR.exists() and data.get("company_id"):
        id2name = {}
        for line in COMPANIES.read_text(encoding="utf-8").splitlines():
            if line.strip():
                dd = json.loads(line)
                id2name[dd["company_id"]] = dd.get("company_name", "")
        comps = []
        for line in SIMILAR.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            s = json.loads(line)
            if s.get("company_id") == data["company_id"]:
                comps.append(id2name.get(s.get("similar_company_id"), "?"))
        if comps:
            data["competitors"] = comps[:5]

    # 4) 잡플래닛 리뷰 (resolver로 안전 매칭 — 매칭되면 pros/cons 샘플)
    jp_file = _jobplanet_by_cid().get(cid) if cid else None
    if jp_file:
        reviews = [json.loads(l) for l in (JOBPLANET_DIR / jp_file).read_text(encoding="utf-8").splitlines() if l.strip()]
        reviews.sort(key=lambda r: r.get("date") or "", reverse=True)
        overalls = [r.get("overall") for r in reviews if isinstance(r.get("overall"), (int, float))]
        data["jobplanet"] = {
            "avg_overall": round(sum(overalls) / len(overalls), 1) if overalls else None,
            "count": len(reviews),
            "samples": [{"overall": r.get("overall"), "pros": (r.get("pros") or "")[:180],
                         "cons": (r.get("cons") or "")[:180]} for r in reviews[:12]],
        }

    # 5) 뉴스 (news 테이블, 최근 20개) — 실제 언론사 url + media + key(news.id)
    news = _news_by_cid().get(cid) if cid else None
    if news:
        data["news"] = [{"key": n["key"], "date": (n["date"] or "")[:10], "title": n["title"],
                         "media": n["media"],  # 실제 언론사
                         "body": n["body"], "url": n["url"]} for n in news[:20]]
    return data


def _attach_citations(report: dict, news: list) -> None:
    """[n] 인용 → source_keys(키 문자열만) + evidence_type 으로 변환 (v2).

    'text [3][6]' → {'text':'text', 'source_keys':[3·6번 뉴스의 key], 'evidence_type':'sourced'}.
    인용 없으면 source_keys=[] + evidence_type='inference' (= AI 해석, 기사 근거 아님).
    실제 url/media/title은 보고서 맨 아래 전역 sources 에서만 관리 (중복·불일치 방지).
    """
    def conv(s, base="inference"):
        # base = 뉴스 인용이 없을 때의 근거 종류 (섹션 성격에 따라 dart/jobplanet/inference)
        if not isinstance(s, str):
            return s
        nums = [int(x) for x in re.findall(r"\[(\d+)\]", s)]
        has_profile = bool(re.search(r"\[[Pp]\]", s))   # 회사 프로필 기반 사실 표시
        text = re.sub(r"\s*\[[Pp]\]", "", re.sub(r"\s*\[\d+\]", "", s)).strip()
        seen, keys = set(), []
        for n in nums:
            if 1 <= n <= len(news):
                k = news[n - 1].get("key")
                if k and k not in seen:
                    seen.add(k)
                    keys.append(k)
        etype = "news" if keys else ("profile" if has_profile else base)
        return {"text": text, "source_keys": keys, "evidence_type": etype}

    # 섹션별 기본 근거 종류 (뉴스 인용 없을 때): 재무=dart, 잡플=jobplanet, 그 외=inference
    sec_base = {"industry_status": "inference", "recent_trends": "inference",
                "growth_potential": "inference", "financial_analysis": "dart",
                "jobplanet_review_summary": "jobplanet"}
    for k, base in sec_base.items():
        sec = report.get(k)
        if isinstance(sec, dict) and isinstance(sec.get("evidence"), list):
            sec["evidence"] = [conv(e, base) for e in sec["evidence"]]
    for k in ("swot_strengths", "swot_weaknesses", "swot_opportunities", "swot_threats"):
        if isinstance(report.get(k), list):
            report[k] = [conv(e, "inference") for e in report[k]]
    if isinstance(report.get("key_points"), list):
        report["key_points"] = [conv(e, "inference") for e in report["key_points"]]


def finalize_report(report: dict, data: dict, company: str) -> dict:
    """메타데이터 + 전역 소스 목록 추가 (v2: source_key 기반)."""
    _attach_citations(report, data.get("news", []))   # [n] → source_keys + evidence_type
    report["company_id"] = data.get("company_id")
    report["company_name"] = company
    report["analysis_version"] = "v2"
    report["generated_at"] = datetime.date.today().isoformat()
    report["source_snapshot"] = {
        "news_count": len(data.get("news", [])),
        "jobplanet_review_count": (data.get("jobplanet") or {}).get("count", 0),
        "has_dart": bool(data.get("financial_indicators") or data.get("financial_audit")
                         or data.get("financial_interim")),
        "has_company_profile": bool(data.get("basic") or data.get("business_description")
                                    or data.get("talent_values")
                                    or data.get("main_products_services")),
        "has_recruit_data": bool(data.get("has_recruit")),
    }
    # 전역 sources — evidence의 source_keys가 이 배열의 source_key를 참조 (메타는 여기서만 관리)
    report["sources"] = [{"source_key": n.get("key"), "source_type": "news",
                          "media": n.get("media"), "title": n.get("title"),
                          "url": n.get("url"), "date": n.get("date")}
                         for n in data.get("news", [])]
    return report


def build_prompt(data: dict) -> str:
    # 뉴스에 인용번호 [1]..[N] 부여 → LLM이 evidence/SWOT에서 이 번호로 출처 인용
    news_block = "\n".join(f"[{i}] ({n.get('media','')}, {n['date']}) {n['title']}: {n['body']}"
                           for i, n in enumerate(data.get("news", []), 1))
    return (
        f"회사: {data['company_name']}\n\n"
        f"[기본정보]\n{json.dumps(data.get('basic', {}), ensure_ascii=False)}\n\n"
        f"[사업설명]\n{data.get('business_description') or '(없음)'}\n\n"
        f"[주요 제품/서비스]\n{json.dumps(data.get('main_products_services') or [], ensure_ascii=False)}\n\n"
        f"[인재상]\n{data.get('talent_values') or '(없음)'}\n\n"
        f"[재무지표(DART)]\n{json.dumps(data.get('financial_indicators', {}), ensure_ascii=False) if data.get('financial_indicators') else '(없음)'}\n\n"
        f"[감사보고서 재무(DART, 단위 원)]\n{json.dumps(data.get('financial_audit', {}), ensure_ascii=False) if data.get('financial_audit') else '(없음)'}\n\n"
        f"[최근 분기/반기 재무(DART, 단위 원)]\n{json.dumps(data.get('financial_interim', {}), ensure_ascii=False) if data.get('financial_interim') else '(없음)'}\n\n"
        f"[직원현황(DART)]\n{json.dumps(data.get('employees', {}), ensure_ascii=False) if data.get('employees') else '(없음)'}\n\n"
        f"[연혁(잡코리아, 최신순)]\n{chr(10).join('- ' + h for h in data.get('history', [])) if data.get('history') else '(없음)'}\n\n"
        f"[잡플래닛 리뷰]\n{json.dumps(data.get('jobplanet', {}), ensure_ascii=False) if data.get('jobplanet') else '(없음)'}\n\n"
        f"[최근 뉴스]\n{news_block or '(없음)'}\n\n"
        "위 데이터만 근거로 기업 분석 보고서를 작성해줘 "
        "(업계현황·최근동향·재무분석·성장잠재력·SWOT·핵심포인트).\n"
        "뉴스를 근거로 쓴 evidence·SWOT 항목 끝에는 그 뉴스의 번호를 [n] 형식으로 단다 "
        "(여러 개면 [n][m]). 위 [최근 뉴스] 목록의 번호만 쓰고, 없는 번호는 쓰지 마라. "
        "DART·잡플래닛 등 뉴스가 아닌 근거엔 번호를 달지 않는다."
    )


def call_openai(prompt: str, schema: dict) -> dict:
    body = {
        "model": OPENAI_MODEL, "temperature": 0.3, "max_output_tokens": 4000,
        "input": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        "text": {"format": {"type": "json_schema", "name": "company_report", "schema": schema, "strict": True}},
    }
    headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"}
    for attempt in range(4):
        try:
            resp = requests.post(OPENAI_URL, headers=headers, json=body, timeout=180)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            time.sleep(2 ** attempt); continue   # 네트워크 타임아웃 재시도
        if "invalid_api_key" in resp.text:
            raise SystemExit("OPENAI_API_KEY가 올바르지 않습니다.")
        if resp.status_code in {429, 500, 502, 503, 504}:
            time.sleep(2 ** attempt); continue
        resp.raise_for_status()
        d = resp.json()
        text = d.get("output_text") or next(
            c["text"] for it in d.get("output", []) for c in it.get("content", []) if c.get("text"))
        return json.loads(text)
    raise RuntimeError("OpenAI 호출 실패")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("company", nargs="?", default="CJ ENM")
    args = ap.parse_args()
    load_dotenv_file(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY 없음. .env 확인.")

    print(f"[{args.company}] 데이터 수집 중...")
    data = gather(args.company)
    have = [k for k in ("basic", "talent_values", "main_products_services",
                        "financial_indicators", "financial_audit",
                        "financial_interim", "employees", "competitors", "jobplanet", "news") if data.get(k)]
    print(f"  수집된 소스: {have}")

    print(f"보고서 생성 중... (모델 {OPENAI_MODEL})")
    report = call_openai(build_prompt(data), build_schema(data))
    report = finalize_report(report, data, args.company)

    print("\n" + "=" * 60)
    print(f"📊 {args.company} 기업 분석 보고서")
    print("=" * 60)
    def show(label, key):
        sec = report.get(key)
        if not sec:
            return  # 데이터 없는 섹션은 넘김
        print(f"\n[{label}] {sec['summary']}")
        if sec.get("evidence"):
            print(f"   └ 근거: {' | '.join(sec['evidence'][:3])}")
    show("업계현황", "industry_status")
    show("최근동향", "recent_trends")
    show("재무분석", "financial_analysis")     # 없으면 자동 스킵
    show("잡플래닛", "jobplanet_review_summary")  # 없으면 자동 스킵
    show("성장성", "growth_potential")
    print(f"\n[강점 S] " + " / ".join(report["swot_strengths"]))
    print(f"[약점 W] " + " / ".join(report["swot_weaknesses"]))
    print(f"[기회 O] " + " / ".join(report["swot_opportunities"]))
    print(f"[위협 T] " + " / ".join(report["swot_threats"]))
    print(f"\n[핵심포인트]")
    for i, p in enumerate(report["key_points"], 1):
        print(f"  {i}. {p}")

    out = ROOT / f"report_{args.company}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {out.name}  (→ company_analyses 적재용)")


if __name__ == "__main__":
    main()
