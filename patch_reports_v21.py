"""v2 보고서 후처리 패치 (LLM 0): 잔여 태그 제거 + evidence_type 재분류.

GPT 리뷰 반영:
- 잔여 [잡플래닛]/[DART]/[감사보고서]/[연혁]/[사업설명] 등 출처태그 → text에서 제거
- 그 태그 기반으로 evidence_type 보정 (SWOT/key_points의 과도한 inference 교정)
- history 타입 신설 ([연혁] 근거)
우선순위: news(source_keys 있음) > 명시태그(jobplanet/dart/history/profile) > 기존값
"""
import json, glob, re
from collections import Counter

# 태그 → evidence_type
def tag_type(t: str):
    if re.search(r"\[잡플", t):
        return "jobplanet"
    if re.search(r"\[(감사보고서|DART|재무지표|재무보고서|재무 데이터|별도|연결|분기|반기|사업보고서)", t):
        return "dart"
    if "[연혁]" in t:
        return "history"
    if re.search(r"\[(사업설명|기본정보|인재상)", t):
        return "profile"
    return None

# 잔여 출처태그 제거 (짧은 토큰 [n][D] + 키워드 태그). 일반 괄호는 보존.
_KW = ["잡플", "감사보고서", "DART", "재무", "연혁", "사업설명", "기본정보", "인재상",
       "별도", "연결", "직원현황", "분기", "반기", "사업보고서", "데이터 없음", "부재",
       "인용", "출처", "리뷰 종합"]
def strip_tags(t: str) -> str:
    def rm(m):
        inner = m.group(1)
        if len(inner) <= 3 or any(k in inner for k in _KW):
            return ""
        return m.group(0)
    return re.sub(r"\s*\[([^\]]{0,18})\]", rm, t).strip()

def fix_item(e, base):
    if not isinstance(e, dict):
        return e, False
    text = e.get("text", "")
    keys = e.get("source_keys") or []
    cur = e.get("evidence_type")
    # 재분류
    if keys:
        nt = "news"
    else:
        tt = tag_type(text)
        nt = tt or ("profile" if cur == "profile" else (cur or base))
    cleaned = strip_tags(text)
    changed = (cleaned != text) or (nt != cur)
    e["text"] = cleaned
    e["evidence_type"] = nt
    return e, changed

SEC_BASE = {"financial_analysis": "dart", "jobplanet_review_summary": "jobplanet"}

def main():
    files = [f for f in glob.glob("reports/*.json")
             if json.load(open(f, encoding="utf-8")).get("analysis_version") == "v2"]
    stats = Counter()
    changed_files = 0
    for f in files:
        r = json.load(open(f, encoding="utf-8"))
        fchanged = False
        for k in ("industry_status", "recent_trends", "financial_analysis",
                  "jobplanet_review_summary", "growth_potential"):
            sec = r.get(k)
            if isinstance(sec, dict) and isinstance(sec.get("evidence"), list):
                base = SEC_BASE.get(k, "inference")
                new = []
                for e in sec["evidence"]:
                    e2, ch = fix_item(e, base)
                    new.append(e2); fchanged |= ch
                    if ch: stats[e2.get("evidence_type")] += 1
                sec["evidence"] = new
        for k in ("swot_strengths", "swot_weaknesses", "swot_opportunities", "swot_threats"):
            if isinstance(r.get(k), list):
                new = []
                for e in r[k]:
                    e2, ch = fix_item(e, "inference")
                    new.append(e2); fchanged |= ch
                    if ch: stats[e2.get("evidence_type")] += 1
                r[k] = new
        if isinstance(r.get("key_points"), list):
            new = []
            for e in r["key_points"]:
                e2, ch = fix_item(e, "inference")
                new.append(e2); fchanged |= ch
                if ch: stats[e2.get("evidence_type")] += 1
            r["key_points"] = new
        if fchanged:
            json.dump(r, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            changed_files += 1
    print(f"패치된 파일: {changed_files}/{len(files)}")
    print(f"변경된 항목의 새 타입 분포: {dict(stats)}")

if __name__ == "__main__":
    main()
