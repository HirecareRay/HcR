"""기업 인재상 수집 실행 진입점.

companies.txt의 각 기업에 대해 공식 홈페이지를 '검증'한 뒤
인재상/사업내용을 할루시네이션 없이 수집해 CSV + JSON으로 저장한다.
"""
import csv
import json
import os
import time

from crawler.config import DELAY_BETWEEN_COMPANIES
from crawler.models import Result, Status
from crawler.pipeline import process_company

CSV_FIELDS = [
    "company_name", "status", "official_url", "url_verified", "verify_reason",
    "talent_values", "talent_values_source", "business_description",
    "reference_talent_values", "reference_urls", "error",
]


def load_companies(filepath: str) -> list[str]:
    with open(filepath, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def _csv_row(result: Result) -> dict:
    data = result.to_dict()
    data["reference_urls"] = " | ".join(data["reference_urls"])
    return data


def append_csv_row(filepath: str, result: Result, write_header: bool) -> None:
    with open(filepath, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(_csv_row(result))


def _print_outcome(result: Result) -> None:
    if result.status == Status.SUCCESS:
        print(f"  → 인재상 수집 완료 ({result.official_url})")
    elif result.status == Status.REFERENCE_ONLY:
        print(f"  → 홈페이지엔 없음, 외부 참고 {len(result.reference_urls)}건에서 수집")
    elif result.status == Status.NO_DATA:
        print("  → 인재상 정보 없음(None)")
    else:
        print(f"  → 실패[{result.status.value}]: {result.error or result.verify_reason}")


def main() -> None:
    companies = load_companies("companies.txt")
    total = len(companies)
    print(f"총 {total}개 기업 인재상 수집 시작\n")

    os.makedirs("output", exist_ok=True)
    csv_path = "output/results.csv"
    json_path = "output/results.json"
    results: list[dict] = []
    counters: dict[str, int] = {}

    for i, company in enumerate(companies, start=1):
        print(f"[{i}/{total}] {company} 처리 중...", flush=True)
        result = process_company(company)
        _print_outcome(result)

        append_csv_row(csv_path, result, write_header=(i == 1))
        results.append(result.to_dict())
        counters[result.status.value] = counters.get(result.status.value, 0) + 1

        if i < total:
            time.sleep(DELAY_BETWEEN_COMPANIES)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n완료! CSV: {csv_path} / JSON: {json_path}")
    print("상태별 집계:")
    for status, count in sorted(counters.items()):
        print(f"  - {status}: {count}")


if __name__ == "__main__":
    main()
