"""jobs_all_deduplicated_v1.jsonl(상세 풀버전) → Mongo job_postings 적재.

채용공고 원본은 work_conditions/jobs/process/common 등 중첩구조라 MariaDB(flat)엔
상세가 안 들어간다. Mongo에 중첩 그대로 올린다.

company_id(ObjectId): company_name → Mongo companies._id 로 붙인다(정확→정규화).
  - url 매칭은 안 씀: gamejob 33건이 잘린 동일 url을 공유해 잘못 묶이기 때문.
_id: auto ObjectId. (source_url·dedupe_id 둘 다 유니크가 아니라 합성키 불가 →
  drop 후 insert 로 컬렉션 단위 멱등 보장.)

실행:  PYTHONPATH=hcr-backend hcr-backend/.venv/bin/python load_job_postings_rich.py
포트: 터널이 13306/37017 이면 그쪽으로 자동 폴백 시도 안 함 — settings 기본(3306/27017).
"""
import json, re, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
from app.core.config import settings
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "hiring_preprocess/data/processed/jobs_all_deduplicated_v1.jsonl"
# 안정 터널 포트(13306/37017)가 열려 있으면 그쪽으로. 아니면 기본.
import socket
def _alive(port):
    s = socket.socket(); s.settimeout(0.3)
    try:
        s.connect(("127.0.0.1", port)); return True
    except OSError:
        return False
    finally:
        s.close()
MONGO = settings.mongodb_uri.replace(":27017/", ":37017/") if _alive(37017) else settings.mongodb_uri

db = MongoClient(MONGO, serverSelectionTimeoutMS=8000)[settings.mongodb_db_name]

def norm(s: str) -> str:
    return re.sub(r"[\s㈜()()주식회사]|Co\.,?Ltd\.?|Inc\.?", "", (s or "")).lower()

# companies 이름 인덱스: 정확 + 정규화(모호한 것 제외)
exact, norm_groups = {}, {}
for c in db["companies"].find({}, {"company_name": 1}):
    nm = c.get("company_name")
    if not nm:
        continue
    exact.setdefault(nm, c["_id"])
    norm_groups.setdefault(norm(nm), set()).add(c["_id"])
norm_uniq = {k: next(iter(v)) for k, v in norm_groups.items() if len(v) == 1}

def resolve(name):
    if name in exact:
        return exact[name]
    return norm_uniq.get(norm(name))  # 모호하면 None

recs = [json.loads(l) for l in open(SRC, encoding="utf-8") if l.strip()]
cidn = 0
for r in recs:
    cid = resolve(r.get("company_name"))
    r["company_id"] = cid
    if cid:
        cidn += 1

db["job_postings"].drop()
db["job_postings"].insert_many(recs, ordered=False)  # auto _id
db["job_postings"].create_index("company_id")
db["job_postings"].create_index("source_url")
print(f"적재 완료: 총 {db['job_postings'].count_documents({})}건 "
      f"| company_id 있음 {cidn} / null {len(recs) - cidn}")
