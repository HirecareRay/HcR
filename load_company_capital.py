"""companies_enriched_v2.jsonl → companies.capital(자본금) 적재.

자본금은 매출액(revenue)과 같은 핵심 재무 팩트이고 커버리지가 넓어(jobkorea·incruit
raw 기준 73%, 799개) top-level canonical 필드로 적재한다. catch raw엔 자본금이 없다.

값 출처: source_profiles[].raw_company_info 의
  - incruit:  basic_info.자본금
  - jobkorea: 기업상세정보.자본금
날짜 꼬리표("... | (2025.12.31)")는 금액만 남기고 제거.

company_id = company_name → Mongo companies._id (정확→정규화). $set 이라 멱등.
실행: (hcr-backend cwd) PYTHONPATH=. python /Users/monapark/HcR/load_company_capital.py
"""
import json, re, warnings
warnings.filterwarnings("ignore")
from app.core.config import settings
from pymongo import MongoClient, UpdateOne

SRC = "/Users/monapark/HcR/hiring_preprocess/data/processed/companies_enriched_v2.jsonl"
db = MongoClient(settings.mongodb_uri.replace(":27017/", ":37017/"),
                 serverSelectionTimeoutMS=8000)[settings.mongodb_db_name]


def norm(s):
    return re.sub(r"[\s㈜()()주식회사]", "", (s or "")).lower()

exact, ng = {}, {}
for c in db["companies"].find({}, {"company_name": 1}):
    nm = c.get("company_name")
    if nm:
        exact.setdefault(nm, c["_id"])
        ng.setdefault(norm(nm), set()).add(c["_id"])
nuniq = {k: next(iter(v)) for k, v in ng.items() if len(v) == 1}

def resolve(name):
    return exact.get(name) or nuniq.get(norm(name))

def capital_of(d):
    for sp in (d.get("source_profiles") or []):
        rci = sp.get("raw_company_info") or {}
        c = ((rci.get("basic_info") or {}).get("자본금")
             or (rci.get("기업상세정보") or {}).get("자본금")
             or rci.get("자본금"))
        if c and str(c).strip():
            return str(c).split("|")[0].strip()   # 날짜 꼬리표 제거
    return None

ops, nocid = [], 0
for line in open(SRC, encoding="utf-8"):
    if not line.strip():
        continue
    d = json.loads(line)
    cap = capital_of(d)
    if not cap:
        continue
    cid = resolve(d.get("company_name"))
    if not cid:
        nocid += 1
        continue
    ops.append(UpdateOne({"_id": cid}, {"$set": {"capital": cap}}))

res = db["companies"].bulk_write(ops, ordered=False)
print(f"자본금 적재: {len(ops)}개 (cid 못붙음 {nocid}) modified={res.modified_count}")
print("companies.capital 보유:", db["companies"].count_documents({"capital": {"$exists": True}}))
