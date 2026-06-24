"""회사명 → company_id 안전 매칭 (exact → normalize → safe_alias → 존재확인).

원칙: 오매칭 하나가 미매칭 열 개보다 위험. fuzzy 자동매칭 금지.
판정: exact/normalize/safe_alias 까지만, alias 결과가 companies에 실재할 때만 연결.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# 안전 alias — normalize된 소스이름 → companies_enriched에 실제 존재하는 이름.
# (확실한 것만. 자동 fuzzy 절대 금지: nc→Inc, 커넥트웨이브→웨이브 같은 오매칭 차단)
SAFE_ALIASES = {
    "씨제이이앤엠": "CJ ENM",
    "씨제이대한통운": "CJ대한통운",
    "씨제이대한통운건설부문": "CJ대한통운",
    "씨제이올리브영": "CJ올리브영",
    "씨제이제일제당": "CJ제일제당",
    "ff": "F&F",
    "jypent": "JYP엔터테인먼트",
    "jypentertainment": "JYP엔터테인먼트",
}

_REMOVE_TOKENS = [
    "주식회사", "(주)", "㈜", "주)",
    "co.,ltd.", "co.ltd", "co ltd", "ltd.",
    "inc.", "inc", "corp.", "corp", "corporation",
    "company", "holdings", "group",
    " ", ".", ",", "-", "_",
]


def normalize_company_name(name: str) -> str:
    if not name:
        return ""
    name = name.lower().strip()
    # 끝에 붙은 괄호(영문명·유한 표기 등) 제거: "와커스(WACUS)"·"㈜케이에스씨앤씨(KSC&C Inc.)" → 본명만
    # 회사목록 충돌 0개 검증 완료 (서로 다른 회사가 같은 키로 합쳐지지 않음).
    prev = None
    while name != prev:
        prev = name
        name = re.sub(r"\([^)]*\)\s*$", "", name).strip()
    for token in _REMOVE_TOKENS:
        name = name.replace(token, "")
    name = name.replace("&", "앤")
    return name


def build_index(companies: list[tuple[str, list[str]]]) -> dict[str, str]:
    """[(company_id, [name, *aliases])] → {normalize: company_id}."""
    idx: dict[str, str] = {}
    for cid, names in companies:
        for n in names:
            key = normalize_company_name(n)
            if key:
                idx.setdefault(key, cid)
    return idx


def resolve(name: str, index: dict[str, str]) -> str | None:
    """이름 → company_id. 못 찾으면 None (절대 추측 안 함)."""
    n = normalize_company_name(name)
    if not n:
        return None
    if n in index:                       # 1·2) exact / normalize
        return index[n]
    if n in SAFE_ALIASES:                # 3) safe alias
        alt = normalize_company_name(SAFE_ALIASES[n])
        if alt in index:                 #    alias 결과가 companies에 실재할 때만
            return index[alt]
    return None                          # 4) unmatched (fuzzy 금지)


def load_companies(companies_jsonl: Path) -> list[tuple[str, list[str]]]:
    rows = []
    for line in companies_jsonl.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            rows.append((d["company_id"], [d.get("company_name", "")] + d.get("aliases", [])))
    return rows


def load_company_index(companies_jsonl: Path) -> dict[str, str]:
    return build_index(load_companies(companies_jsonl))


def fuzzy_candidate(name: str, companies: list[tuple[str, list[str]]]) -> str | None:
    """⚠️ 진단 전용 — 자동매칭에 쓰지 마라. 부분일치 후보가 있으면 반환.
    unmatched 사유 분류용: 후보 있으면 unsafe_fuzzy, 없으면 not_in_companies."""
    n = normalize_company_name(name)
    if len(n) < 2:
        return None
    for _cid, names in companies:
        for cand in names:
            c = normalize_company_name(cand)
            if c and min(len(n), len(c)) >= 2 and (n in c or c in n):
                return cand
    return None
