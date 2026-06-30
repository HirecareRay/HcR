"""catch 수집 → 분리 적재 (재실행 안전).

- company_pages: source_site="catch" 원천/아카이브. 서술형 전문은 profile.etc 에만.
  upsert 기준: {source_site:"catch", company_page_url}  (페이지 1:1, 재실행 시 교체)
- companies: 구조화 '사실값'만 기존 canonical 필드에 $set (서술형 narrative 절대 X).
  upsert 기준: {_id: company ObjectId}  ($set 이라 멱등)
- catch_company_detail: 드롭

소스: catch_summary_raw.jsonl(API) + catch_company_detail.jsonl(DOM, 초봉). 둘 다 영구파일.
실행: (hcr-backend cwd) PYTHONPATH=. python /Users/monapark/HcR/catch/load_catch_split.py
"""
import json, re, warnings
warnings.filterwarnings("ignore")
from app.core.config import settings
from pymongo import MongoClient, ReplaceOne, UpdateOne
from bson import ObjectId

C = "/Users/monapark/HcR/catch"
RAW = f"{C}/catch_summary_raw.jsonl"
DOM = f"{C}/catch_company_detail.jsonl"
db = MongoClient(settings.mongodb_uri.replace(":27017/", ":37017/"),
                 serverSelectionTimeoutMS=8000)[settings.mongodb_db_name]

# ── company_name → companies._id (정확→정규화, 모호 제외) ──
def norm(s):
    return re.sub(r"[\s㈜()()주식회사]|Co\.,?Ltd\.?|Inc\.?", "", (s or "")).lower()
exact, ng = {}, {}
for c in db["companies"].find({}, {"company_name": 1}):
    nm = c.get("company_name")
    if nm:
        exact.setdefault(nm, c["_id"])
        ng.setdefault(norm(nm), set()).add(c["_id"])
nuniq = {k: next(iter(v)) for k, v in ng.items() if len(v) == 1}
def resolve(name):
    return exact.get(name) or nuniq.get(norm(name))

def strip_html(t):
    return (re.sub(r"<[^>]+>", "", t).replace("\r\n", "\n").strip() or None) if t else None
def sal(v):
    try: return f"{int(v):,}만원"
    except (ValueError, TypeError): return None

# ── DOM 초봉 ──
dom_entry = {}
for line in open(DOM, encoding="utf-8"):
    if line.strip():
        d = json.loads(line)
        code = (d.get("source_url") or "").rstrip("/").split("/")[-1]
        if code and d.get("entry_salary"):
            dom_entry[code] = d["entry_salary"]

def fields(summary, code):
    s = summary or {}
    one = s.get("oneWord") or {}
    ai = s.get("aiCompSummary") or {}
    rs = s.get("recruitSize") or {}
    sl = s.get("salaryList") or []
    comp = next((x for x in sl if not x.get("IsJinhakCodeAvg")), None)
    ind = next((x for x in sl if x.get("IsJinhakCodeAvg")), None)
    rv = lambda k: (rs.get(k) or {}).get("Value")
    sections = {k: strip_html(v) for k, v in (ai.get("sections") or {}).items()}
    facts = {  # companies 로 가는 '사실값'
        "entry_salary": dom_entry.get(code),
        "average_salary": sal(comp.get("Salary")) if comp else None,
        "industry_avg_salary": sal(ind.get("Salary")) if ind else None,
        "recruit_scale": (f"{int(rv('RecruitSize')):,}명" if rv("RecruitSize") is not None else None),
        "turnover_rate": (f"{rv('Turnover')}%" if rv("Turnover") is not None else None),
        "avg_work_year": rv("WorkYear"),
        "brand_list": [{"name": b.get("BrandName"), "desc": strip_html(b.get("BrandDesc"))}
                       for b in (s.get("brandList") or [])] or None,
    }
    narrative = {  # company_pages.etc 로만 가는 서술형
        "company_introduction": strip_html(one.get("CompCmt")),
        "ai_intro": (ai.get("meta") or {}).get("compIntro"),
        "ai_sections": sections,
    }
    return facts, narrative

comp_ops, page_ops = [], []
matched = no_cid = 0
for line in open(RAW, encoding="utf-8"):
    if not line.strip():
        continue
    r = json.loads(line)
    if not r.get("ok"):
        continue
    code, name = r.get("code"), r.get("company_name")
    cid = resolve(name)
    facts, narrative = fields(r.get("summary"), code)
    url = f"https://www.catch.co.kr/Comp/CompSummary/{code}"

    # companies: 사실값만. UI 노출 필드는 flat, 안 쓰는 2개는 etc 하위로.
    FLAT = ("entry_salary", "average_salary", "turnover_rate", "avg_work_year", "brand_list")
    ETC = ("industry_avg_salary", "recruit_scale")   # UI 미노출 → companies.etc
    set_doc = {k: facts[k] for k in FLAT if facts.get(k) not in (None, [], "")}
    for k in ETC:
        if facts.get(k) not in (None, [], ""):
            set_doc[f"etc.{k}"] = facts[k]
    if cid and set_doc:
        # 옛 flat 위치(이전 적재분) 제거 후 etc로 이동
        comp_ops.append(UpdateOne({"_id": cid},
                                  {"$set": set_doc, "$unset": {"industry_avg_salary": "", "recruit_scale": ""}}))
        matched += 1
    else:
        no_cid += 1

    # company_pages: 전문(사실+서술) etc 보존
    etc = {**{k: v for k, v in facts.items() if v not in (None, [], "")}, **{k: v for k, v in narrative.items() if v}}
    page = {
        "source_site": "catch", "company_page_url": url,
        "crawl_success": True, "http_status": 200,
        "company_id": cid, "error": None,
        "profile": {"company_name": name,
                    "entry_salary": facts["entry_salary"],
                    "average_salary": facts["average_salary"],
                    "etc": etc},
    }
    page_ops.append(ReplaceOne({"source_site": "catch", "company_page_url": url}, page, upsert=True))

cr = db["companies"].bulk_write(comp_ops, ordered=False) if comp_ops else None
pr = db["company_pages"].bulk_write(page_ops, ordered=False)
db["catch_company_detail"].drop()

print(f"companies $set: {matched}개 (cid 못붙음 {no_cid})  modified={getattr(cr,'modified_count',0)}")
print(f"company_pages catch upsert: {len(page_ops)}  upserted={pr.upserted_count} modified={pr.modified_count}")
print("company_pages 총:", db["company_pages"].count_documents({}),
      "| catch:", db["company_pages"].count_documents({"source_site": "catch"}))
print("catch_company_detail 드롭됨:", "catch_company_detail" not in db.list_collection_names())
# 검증 샘플
c = db["companies"].find_one({"_id": resolve("BGF리테일")}, {"brand_list": 1, "entry_salary": 1, "turnover_rate": 1, "company_introduction": 1})
print("\n검증 BGF companies:", {k: (v if k != "brand_list" else f"{len(v)}개") for k, v in c.items() if k != "_id"})
print("  → company_introduction 없어야 정상:", "company_introduction" not in c)
