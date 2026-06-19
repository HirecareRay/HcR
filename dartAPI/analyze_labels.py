"""감사보고서 표본의 손익계산서(IS) 라벨 분포 + 현재 파서 성공률 분석 (1회성 조사용).

companies.txt에서 시드 고정 표본 추출 → 각 회사 최신 감사보고서 다운로드 →
  1) 현재 _extract_financial_figures()의 revenue/oi/ni 성공 여부
  2) IS 섹션의 라벨을 정규화해 매출/영업이익/당기순이익 후보 분포 집계

실행: python analyze_labels.py [표본수] [시드]
"""
import re
import sys
import time
from collections import Counter

import dart_audit as da
from corp_code import get_corp_code

SAMPLE_SIZE = int(sys.argv[1]) if len(sys.argv) > 1 else 30
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 42
COMPANIES_TXT = "../company-crawler/companies.txt"

# 머리번호(로마숫자·아라비아·괄호)·공백·구두점 제거 → 의미 라벨만 남긴다
_ENUM = re.compile(r"^[\sⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ0-9\.\)\(（）.·\-－]+")


def normalize(label: str) -> str:
    s = label.replace("\xa0", "").replace(" ", "").strip()
    s = _ENUM.sub("", s)
    return s


def load_companies(path: str) -> list[str]:
    import random
    with open(path, encoding="utf-8") as f:
        pool = [ln.strip() for ln in f if ln.strip()]
    return random.Random(SEED).sample(pool, min(SAMPLE_SIZE, len(pool)))


def load_unlisted_corp_codes() -> list[tuple[str, str]]:
    """corp_codes.csv에서 stock_code가 빈(외감 비상장) 기업을 시드 고정 표본 추출."""
    import csv
    import random
    rows: list[tuple[str, str]] = []
    with open("data/corp_codes.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not (r.get("stock_code") or "").strip():
                rows.append((r["corp_code"], r["corp_name"]))
    return random.Random(SEED).sample(rows, min(SAMPLE_SIZE, len(rows)))


def classify_is_labels(is_keys: list[str]) -> dict:
    """IS 섹션 라벨에서 revenue/oi/ni 후보 라벨을 정규화 기준으로 고른다."""
    out = {"revenue": None, "operating_income": None, "net_income": None}
    for raw in is_keys:
        n = normalize(raw)
        if out["revenue"] is None and (
            n in ("매출", "매출액", "영업수익", "수익", "수익(매출액)", "영업수익(매출액)")
            or (n.startswith("매출") and not any(
                x in n for x in ("원가", "총이익", "채권", "총액공제", "할인", "환입")))
            and "매출" == n[:2] and len(n) <= 6
        ):
            out["revenue"] = n
        if out["operating_income"] is None and n in ("영업이익", "영업손실", "영업이익(손실)"):
            out["operating_income"] = n
        if out["net_income"] is None and (
            n.startswith("당기순이익") or n.startswith("당기순손실")
            or n in ("분기순이익", "반기순이익", "당기순이익(손실)")):
            out["net_income"] = n
    return out


def main() -> None:
    sample_cc = load_unlisted_corp_codes()
    print(f"외감 비상장 표본 {len(sample_cc)}개 (seed={SEED})\n", flush=True)

    rev_labels: Counter = Counter()
    oi_labels: Counter = Counter()
    ni_labels: Counter = Counter()
    stat = Counter()
    fail_rows: list[dict] = []

    for i, (cc, name) in enumerate(sample_cc, 1):
        print(f"[{i}/{len(sample_cc)}] {name} ({cc})", flush=True)
        try:
            reports = da.get_audit_reports(cc)
            if not reports:
                stat["감사보고서_없음"] += 1
                continue
            rcept = sorted(reports, key=lambda r: r["rcept_dt"], reverse=True)[0]["rcept_no"]
            zb = da._download_report_zip(rcept)
            soup = da._load_main_xml(zb, rcept) if zb else None
            if not soup:
                stat["다운로드_실패"] += 1
                continue

            fig = da._extract_financial_figures(soup)
            tables = da._extract_all_financial_tables(soup)
            is_keys = list(tables.get("IS", {}).keys())
            # IS가 비면 손익계산서가 CIS(포괄손익)로 잡히는 경우 대비
            if not is_keys:
                is_keys = list(tables.get("CIS", {}).keys())
            cls = classify_is_labels(is_keys)
            if cls["revenue"]:
                rev_labels[cls["revenue"]] += 1
            if cls["operating_income"]:
                oi_labels[cls["operating_income"]] += 1
            if cls["net_income"]:
                ni_labels[cls["net_income"]] += 1

            missing = [k for k in ("revenue", "operating_income", "net_income")
                       if fig.get(k) is None]
            if missing:
                stat["파서_일부실패"] += 1
                fail_rows.append({"name": name, "rcept": rcept,
                                  "missing": ",".join(missing),
                                  "is_revenue_label": cls["revenue"],
                                  "is_keys_head": is_keys[:8]})
            else:
                stat["파서_전부성공"] += 1
        except Exception as exc:
            stat["예외"] += 1
            print(f"    예외: {exc}", flush=True)
        time.sleep(0.4)

    print("\n===== 처리 결과 =====")
    for k, v in stat.most_common():
        print(f"  {k:16} {v}")

    print("\n===== revenue 라벨 분포 (정규화 후) =====")
    for k, v in rev_labels.most_common():
        print(f"  {v:3}  {k}")
    print("\n===== operating_income 라벨 분포 =====")
    for k, v in oi_labels.most_common():
        print(f"  {v:3}  {k}")
    print("\n===== net_income 라벨 분포 =====")
    for k, v in ni_labels.most_common():
        print(f"  {v:3}  {k}")

    if fail_rows:
        print("\n===== 현재 파서 실패 케이스 =====")
        for r in fail_rows:
            print(f"  [{r['missing']}] {r['name']} (rev라벨={r['is_revenue_label']})")
            print(f"        IS keys: {r['is_keys_head']}")


if __name__ == "__main__":
    main()
