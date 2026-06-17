#!/usr/bin/env python3
"""Clean recruitment TSV files from multiple job platforms into one schema."""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path
from typing import Any

import pandas as pd


STANDARD_COLUMNS = [
    "source",
    "company_original",
    "company_clean",
    "company_dart_query",
    "corp_code",
    "title",
    "job_categories_clean",
    "detail_text",
    "summary_text",
    "deadline",
    "image_url",
    "ceo",
    "corp_type",
    "company_size",
    "industry",
    "address",
    "homepage",
    "founded_date",
    "employees",
    "sales_raw",
    "sales_억원",
    "capital_raw",
    "capital_억원",
    "operating_profit_raw",
    "operating_profit_억원",
    "net_income_raw",
    "net_income_억원",
    "recruit_url",
    "rag_text",
]

TOP_LEVEL_RENAME = {
    "job": "job_categories",
    "detail_img": "image_urls",
    "detail": "detail_text_dup",
    "etc_info": "summary_text",
    "url": "recruit_url",
}

REQUIRED_RAW_COLUMNS = [
    "company",
    "title",
    "job_categories",
    "detail_text",
    "summary_text",
    "company_info",
    "recruit_url",
]

SOURCE_KEYWORDS = {
    "catch": ("catch",),
    "jobkorea": ("jobkorea",),
    "incruit": ("incruit",),
    "saramin": ("saramin", "saram"),
}


def safe_literal_eval(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, (list, dict)):
        return value
    try:
        return ast.literal_eval(str(value))
    except (SyntaxError, ValueError):
        return None


def clean_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def clean_company_name(value: Any) -> str:
    return clean_text(value)


def make_dart_company_query(value: Any) -> str:
    name = clean_company_name(value)
    name = re.sub(r"^(주식회사|\(주\)|㈜)\s*", "", name)
    name = re.sub(r"\s+(주식회사)$", "", name)
    return name.strip()


def clean_integer(value: Any) -> int | None:
    if pd.isna(value):
        return None
    nums = re.sub(r"[^0-9]", "", str(value))
    return int(nums) if nums else None


def money_to_eok(value: Any) -> float | None:
    if pd.isna(value):
        return None

    text = str(value).replace(",", "").replace("|", " ")
    if not re.search(r"\d", text):
        return None

    total = 0.0

    jo_match = re.search(r"(-?\d+(?:\.\d+)?)\s*조", text)
    if jo_match:
        total += float(jo_match.group(1)) * 10000

    eok_match = re.search(r"(-?\d+(?:\.\d+)?)\s*억", text)
    if eok_match:
        total += float(eok_match.group(1))

    man_match = re.search(r"(-?\d+(?:\.\d+)?)\s*만", text)
    if man_match:
        total += float(man_match.group(1)) / 10000
    else:
        cheon_man_match = re.search(r"(-?\d+(?:\.\d+)?)\s*천\s*만", text)
        if cheon_man_match:
            total += float(cheon_man_match.group(1)) * 1000 / 10000

        baek_man_match = re.search(r"(-?\d+(?:\.\d+)?)\s*백\s*만", text)
        if baek_man_match:
            total += float(baek_man_match.group(1)) * 100 / 10000

        sip_man_match = re.search(r"(-?\d+(?:\.\d+)?)\s*십\s*만", text)
        if sip_man_match:
            total += float(sip_man_match.group(1)) * 10 / 10000

    if total:
        return round(total, 4)

    nums = re.sub(r"[^0-9.-]", "", text)
    try:
        return float(nums) if nums else None
    except ValueError:
        return None


def clean_date_from_text(value: Any) -> str | None:
    if pd.isna(value):
        return None
    text = str(value)

    match = re.search(r"\d{4}-\d{2}-\d{2}T", text)
    if match:
        return match.group().replace("T", "")

    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        return match.group()

    match = re.search(r"마감일\s*(\d{4})\.(\d{2})\.(\d{2})", text)
    if match:
        return "-".join(match.groups())

    return None


def get_first_url(value: Any) -> str | None:
    urls = safe_literal_eval(value)
    if isinstance(urls, list) and urls:
        return urls[0]
    text = clean_text(value)
    return text or None


def clean_job_categories(value: Any) -> str:
    categories = safe_literal_eval(value)
    if isinstance(categories, list):
        return ", ".join(clean_text(item) for item in categories if clean_text(item))
    return clean_text(value)


def nested_get(data: dict[str, Any] | None, *keys: str) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def current_value(data: dict[str, Any] | None, key: str) -> Any:
    return nested_get(data, "financial", key, "current_value")


def parse_company_info(source: str, company_info: Any) -> dict[str, Any]:
    info = safe_literal_eval(company_info)
    if not isinstance(info, dict):
        return {}

    if source == "catch":
        sales_raw = info.get("매출액")
        return {
            "ceo": info.get("대표자"),
            "corp_type": info.get("기업형태"),
            "company_size": info.get("기업규모"),
            "industry": info.get("상세업종"),
            "address": info.get("주소"),
            "homepage": None,
            "founded_date": info.get("개업일"),
            "employees": clean_integer(info.get("사원수")),
            "sales_raw": sales_raw,
            "sales_억원": money_to_eok(sales_raw),
            "capital_raw": None,
            "capital_억원": None,
            "operating_profit_raw": None,
            "operating_profit_억원": None,
            "net_income_raw": None,
            "net_income_억원": None,
        }

    basic = info.get("basic_info") if isinstance(info.get("basic_info"), dict) else {}

    if source == "jobkorea":
        sales_raw = basic.get("매출액")
        capital_raw = basic.get("자본금")
        operating_profit_raw = current_value(info, "영업이익")
        net_income_raw = current_value(info, "당기순이익")
        return {
            "ceo": basic.get("대표자"),
            "corp_type": basic.get("기업구분"),
            "company_size": basic.get("기업구분"),
            "industry": basic.get("산업"),
            "address": basic.get("주소"),
            "homepage": basic.get("홈페이지"),
            "founded_date": basic.get("설립일"),
            "employees": clean_integer(basic.get("사원수")),
            "sales_raw": sales_raw,
            "sales_억원": money_to_eok(sales_raw),
            "capital_raw": capital_raw,
            "capital_억원": money_to_eok(capital_raw),
            "operating_profit_raw": operating_profit_raw,
            "operating_profit_억원": money_to_eok(operating_profit_raw),
            "net_income_raw": net_income_raw,
            "net_income_억원": money_to_eok(net_income_raw),
        }

    if source in {"incruit", "saramin"}:
        sales_raw = basic.get("매출액")
        capital_raw = basic.get("자본금")
        return {
            "ceo": basic.get("대표자"),
            "corp_type": None,
            "company_size": basic.get("기업규모"),
            "industry": basic.get("업종"),
            "address": basic.get("주소"),
            "homepage": basic.get("홈페이지"),
            "founded_date": basic.get("설립일"),
            "employees": clean_integer(basic.get("사원수")),
            "sales_raw": sales_raw,
            "sales_억원": money_to_eok(sales_raw),
            "capital_raw": capital_raw,
            "capital_억원": money_to_eok(capital_raw),
            "operating_profit_raw": None,
            "operating_profit_억원": None,
            "net_income_raw": None,
            "net_income_억원": None,
        }

    return {}


def make_rag_text(row: pd.Series) -> str:
    return f"""
[채용공고]
출처: {row.get("source", "")}
회사명: {row.get("company_original", "")}
DART 검색명: {row.get("company_dart_query", "")}
공고명: {row.get("title", "")}

[직무]
{row.get("job_categories_clean", "")}

[채용조건]
{row.get("summary_text", "")}

[상세내용]
{row.get("detail_text", "")}

[기업정보]
대표자: {row.get("ceo", "")}
기업형태: {row.get("corp_type", "")}
기업규모: {row.get("company_size", "")}
업종: {row.get("industry", "")}
주소: {row.get("address", "")}
홈페이지: {row.get("homepage", "")}
설립일: {row.get("founded_date", "")}
사원수: {row.get("employees", "")}
매출액: {row.get("sales_raw", "")}
자본금: {row.get("capital_raw", "")}
영업이익: {row.get("operating_profit_raw", "")}
당기순이익: {row.get("net_income_raw", "")}

[출처]
{row.get("recruit_url", "")}
""".strip()


def read_raw_tsv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep="\t")
    return df.rename(columns=TOP_LEVEL_RENAME)


def clean_file(source: str, path: Path) -> pd.DataFrame:
    df = read_raw_tsv(path)

    missing = [col for col in REQUIRED_RAW_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")

    out = pd.DataFrame(index=df.index)
    out["source"] = source
    out["company_original"] = df["company"].apply(clean_text)
    out["company_clean"] = df["company"].apply(clean_company_name)
    out["company_dart_query"] = df["company"].apply(make_dart_company_query)
    out["corp_code"] = ""
    out["title"] = df["title"].apply(clean_text)
    out["job_categories_clean"] = df["job_categories"].apply(clean_job_categories)
    out["detail_text"] = df["detail_text"].apply(clean_text)
    out["summary_text"] = df["summary_text"].apply(clean_text)
    out["deadline"] = df["detail_text"].apply(clean_date_from_text)
    out["image_url"] = df["image_urls"].apply(get_first_url) if "image_urls" in df.columns else None

    parsed = df["company_info"].apply(lambda value: parse_company_info(source, value))
    out = pd.concat([out, pd.DataFrame(parsed.tolist())], axis=1)
    out["recruit_url"] = df["recruit_url"].apply(clean_text)

    for col in STANDARD_COLUMNS:
        if col not in out.columns:
            out[col] = ""

    out = out.drop_duplicates(subset=["recruit_url"])
    out = out.where(pd.notna(out), "")
    out["rag_text"] = out.apply(make_rag_text, axis=1)
    return out[STANDARD_COLUMNS]


def parse_input(value: str) -> tuple[str, Path]:
    if ":" not in value:
        raise argparse.ArgumentTypeError("Input must use SOURCE:PATH format.")
    source, path = value.split(":", 1)
    source = source.strip().lower()
    if source not in {"catch", "jobkorea", "incruit", "saramin"}:
        raise argparse.ArgumentTypeError(f"Unsupported source: {source}")
    return source, Path(path).expanduser()


def infer_source(path: Path) -> str | None:
    name = path.name.lower()
    for source, keywords in SOURCE_KEYWORDS.items():
        if any(keyword in name for keyword in keywords):
            return source
    return None


def is_raw_input_tsv(path: Path) -> bool:
    try:
        df = pd.read_csv(path, sep="\t", nrows=0)
    except Exception:
        return False
    df = df.rename(columns=TOP_LEVEL_RENAME)
    return all(col in df.columns for col in REQUIRED_RAW_COLUMNS)


def discover_inputs() -> list[tuple[str, Path]]:
    script_dir = Path(__file__).resolve().parent
    search_dirs = [
        script_dir / "data" / "raw",
        script_dir / "test_data",
        Path.cwd() / "data" / "raw",
        Path.cwd() / "test_data",
    ]

    discovered: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    for directory in search_dirs:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.tsv")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)

            source = infer_source(path)
            if source is None:
                print(f"건너뜀: source를 추정할 수 없음 - {path}")
                continue
            if not is_raw_input_tsv(path):
                print(f"건너뜀: 원천 입력 스키마가 아님 - {path}")
                continue

            discovered.append((source, path))

    return discovered


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean recruitment TSV files into a shared schema.")
    parser.add_argument(
        "--input",
        action="append",
        type=parse_input,
        required=False,
        help="Input pair in SOURCE:PATH format. Example: catch:/path/to/file.tsv",
    )
    parser.add_argument(
        "--output",
        required=False,
        help="Output TSV path. Default: outputs/all_jobs_clean.tsv next to this script.",
    )
    parser.add_argument(
        "--split-by-source",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also write one cleaned TSV per source. Default: true.",
    )
    args = parser.parse_args()

    inputs = args.input if args.input else discover_inputs()
    if not inputs:
        raise SystemExit(
            "처리할 TSV를 찾지 못했습니다. --input source:/path/file.tsv 를 주거나 "
            "hiring_preprocess/data/raw 또는 hiring_preprocess/test_data 안에 원천 TSV를 넣어주세요."
        )

    print("입력 파일:")
    for source, path in inputs:
        print(f"- {source}: {path}")

    frames = [clean_file(source, path) for source, path in inputs]
    result = pd.concat(frames, ignore_index=True)
    result = result.drop_duplicates(subset=["source", "recruit_url"])

    output = (
        Path(args.output).expanduser()
        if args.output
        else Path(__file__).resolve().parent / "outputs" / "all_jobs_clean.tsv"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, sep="\t", index=False, encoding="utf-8-sig")

    print(f"전처리 완료: {output}")
    print(f"저장 건수: {len(result)}")
    print("출처별 건수:")
    for source, count in result["source"].value_counts().sort_index().items():
        print(f"- {source}: {count}")

    if args.split_by_source:
        for source, source_df in result.groupby("source", sort=True):
            source_output = output.with_name(f"{source}_jobs_clean.tsv")
            source_df.to_csv(source_output, sep="\t", index=False, encoding="utf-8-sig")
            print(f"개별 저장: {source_output} ({len(source_df)}건)")


if __name__ == "__main__":
    main()
