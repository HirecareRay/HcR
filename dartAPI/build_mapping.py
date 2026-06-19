"""
companies.txt를 DART corp_codes.csv 기준으로 매핑한다.

신뢰할 수 있는 단계만 사용한다 (퍼지 매칭 제거):
  1) 별칭표(data/aliases.csv) 강제 매핑  → 영문/약칭 수동 확정
  2) 정확 일치                          → 동일
  3) 정규화 일치 (법인표기/공백/괄호만 차이) → 표기수정

정규화에서 '&' 제거를 빼서 FF↔F&F 같은 가짜 일치를 막는다.
결과를 data/matched.csv(확정) / data/unmatched.csv(미매칭)로 분리한다.
(비대화형 / 네트워크 없음 — 로컬 CSV만 읽음)
"""
import csv
import re
from pathlib import Path

INPUT = Path("companies.txt")
CORP_CSV = Path("data/corp_codes.csv")
ALIAS_CSV = Path("data/aliases.csv")
OUT_MATCHED = Path("data/matched.csv")
OUT_UNMATCHED = Path("data/unmatched.csv")


def _normalize(name: str) -> str:
    """법인 표기·공백·괄호·점만 제거한다. '&'는 보존(다른 회사 합쳐짐 방지)."""
    name = re.sub(r"주식회사|㈜|\(주\)|\(유\)|유한회사", "", name)
    name = re.sub(r"[\s\(\)\.\,]", "", name)
    return name.strip()


def _load_companies() -> list[str]:
    return [
        line.strip()
        for line in INPUT.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_corp_codes() -> list[dict]:
    with open(CORP_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_aliases() -> dict[str, dict]:
    if not ALIAS_CSV.exists():
        return {}
    with open(ALIAS_CSV, encoding="utf-8") as f:
        return {row["input"]: row for row in csv.DictReader(f)}


def _pick_best(cands: list[dict]) -> dict:
    """후보 중 상장사(stock_code 있음) 우선, 없으면 첫 번째."""
    listed = [c for c in cands if c["stock_code"].strip()]
    return listed[0] if listed else cands[0]


def main() -> None:
    companies = _load_companies()
    records = _load_corp_codes()
    aliases = _load_aliases()

    exact_index: dict[str, list[dict]] = {}
    norm_index: dict[str, list[dict]] = {}
    for r in records:
        exact_index.setdefault(r["corp_name"], []).append(r)
        norm_index.setdefault(_normalize(r["corp_name"]), []).append(r)

    matched: list[dict] = []
    unmatched: list[dict] = []

    for company in companies:
        # 1) 별칭표 강제 매핑
        if company in aliases:
            a = aliases[company]
            matched.append({
                "input": company, "dart": a["dart_name"],
                "corp_code": a["corp_code"], "stock_code": a["stock_code"],
                "type": "별칭",
            })
            continue

        # 2) 정확 일치
        if company in exact_index:
            r = _pick_best(exact_index[company])
            matched.append({
                "input": company, "dart": r["corp_name"],
                "corp_code": r["corp_code"], "stock_code": r["stock_code"],
                "type": "동일",
            })
            continue

        # 3) 정규화 일치 (표기만 다름)
        ni = _normalize(company)
        if ni in norm_index:
            r = _pick_best(norm_index[ni])
            matched.append({
                "input": company, "dart": r["corp_name"],
                "corp_code": r["corp_code"], "stock_code": r["stock_code"],
                "type": "표기수정",
            })
            continue

        # 4) 미매칭
        unmatched.append({"input": company, "norm": ni})

    OUT_MATCHED.parent.mkdir(exist_ok=True)
    with open(OUT_MATCHED, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["input", "dart", "corp_code", "stock_code", "type"]
        )
        w.writeheader()
        w.writerows(matched)

    with open(OUT_UNMATCHED, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["input", "norm"])
        w.writeheader()
        w.writerows(unmatched)

    def cnt(t):
        return sum(1 for r in matched if r["type"] == t)

    total = len(companies)
    print(f"전체 {total}개")
    print(f"  ── 확정(matched.csv) {len(matched)}개 ──")
    print(f"     별칭      {cnt('별칭')}")
    print(f"     동일      {cnt('동일')}")
    print(f"     표기수정  {cnt('표기수정')}")
    print(f"  ── 미매칭(unmatched.csv) {len(unmatched)}개 ──")


if __name__ == "__main__":
    main()
