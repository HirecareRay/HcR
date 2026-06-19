#!/usr/bin/env python3
"""HCR 채용공고 전처리: raw 파일 -> OCR + OpenAI 정규화 JSONL."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from tqdm import tqdm

from audit_utils import audit_path_for, split_record
from quality_utils import (
    remove_track_labels_from_preferred,
    repeated_job_content,
    replace_null_scalars,
    sparse_job_content,
)

try:
    from ocr_utils import (
        build_final_text,
        find_image_urls,
        get_detail_text,
        has_html_text,
        is_low_quality_ocr,
        ocr_image_urls,
    )
except ModuleNotFoundError as error:
    if error.name == "ocr_utils":
        raise SystemExit("ocr_utils.py를 clean_all_jobs.py와 같은 폴더에 두세요.") from error
    raise

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR if (SCRIPT_DIR / "data").exists() else SCRIPT_DIR.parent
def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))

load_dotenv_file(PROJECT_DIR / ".env")
load_dotenv_file(SCRIPT_DIR / ".env")
RAW_DIR = PROJECT_DIR / "data/raw"
PROCESSED_DIR = PROJECT_DIR / "data/processed"
DEFAULT_OUT_PATH = PROCESSED_DIR / "jobs_normalized_v2.jsonl"
OCR_CACHE_PATH = PROJECT_DIR / "data/cache/ocr_cache.json"
LLM_CACHE_PATH = PROJECT_DIR / "data/cache/llm_cache_v2.json"
PAGE_CACHE_PATH = PROJECT_DIR / "data/cache/page_cache.json"
SCHEMA_PATH = SCRIPT_DIR / "job_schema_v2.json"
PROMPT_PATH = SCRIPT_DIR / "normalization_prompt_v2.txt"
OPENAI_URL = "https://api.openai.com/v1/responses"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0"))
MAX_LLM_CHARS = int(os.getenv("MAX_LLM_CHARS", "12000"))
USE_LLM = os.getenv("USE_LLM", "1") != "0"
SCHEMA_VERSION = "hcr_job_schema_v2_8"
SOURCE_URL_COLUMNS = ["source_url", "url", "posting_url", "recruit_url", "link"]
COMPANY_COLUMNS = ["company_name", "company", "company_clean", "company_original"]
TITLE_COLUMNS = ["posting_title", "title", "recruit_title", "job_title"]
NON_FREELANCE_TYPES = ("정규직", "계약직", "인턴", "아르바이트", "파견직")


def first_value(row: dict[str, Any], columns: list[str]) -> str:
    for column in columns:
        value = row.get(column)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""
def load_schema() -> dict[str, Any]:
    if not SCHEMA_PATH.exists():
        raise SystemExit(f"스키마 파일이 없습니다: {SCHEMA_PATH}")
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

JOB_SCHEMA = load_schema()
def make_llm_schema(schema: dict[str, Any]) -> dict[str, Any]:
    llm_schema = copy.deepcopy(schema)
    llm_schema["properties"].pop("raw_meta", None)
    llm_schema["required"].remove("raw_meta")
    job_schema = llm_schema["$defs"]["job"]
    job_schema["properties"].pop("headcount_value", None)
    job_schema["required"].remove("headcount_value")
    log_schema = llm_schema["$defs"]["preprocess_log"]
    log_schema["properties"].pop("original_text_snapshot", None)
    log_schema["required"].remove("original_text_snapshot")
    return llm_schema

LLM_SCHEMA = make_llm_schema(JOB_SCHEMA)
def file_fingerprint(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"필수 파일이 없습니다: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()

PROMPT_VERSION = file_fingerprint(PROMPT_PATH)
LLM_SCHEMA_JSON = json.dumps(LLM_SCHEMA, sort_keys=True, ensure_ascii=False)
LLM_SCHEMA_VERSION = hashlib.sha256(LLM_SCHEMA_JSON.encode("utf-8")).hexdigest()
def read_json_frame(path: Path) -> pd.DataFrame:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("data", "items", "jobs", "records"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    return pd.json_normalize(data)
def load_raw_files(raw_dir: Path = RAW_DIR, input_path: Path | None = None) -> list[pd.DataFrame]:
    frames = []
    paths = [input_path] if input_path else sorted(raw_dir.glob("*"))
    for path in paths:
        if path is None or not path.exists():
            continue
        suffix = path.suffix.lower()
        if suffix == ".csv":
            df = pd.read_csv(path, dtype=str)
        elif suffix == ".tsv":
            df = pd.read_csv(path, sep="\t", dtype=str)
        elif suffix == ".jsonl":
            df = pd.read_json(path, lines=True, dtype=False)
        elif suffix == ".json":
            df = read_json_frame(path)
        else:
            continue
        df = df.fillna("").drop(columns=["company_info"], errors="ignore")
        df["_source_file"] = str(path)
        df["_source_row"] = range(2, len(df) + 2)
        frames.append(df)
    return frames

def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print(f"경고: 손상된 캐시를 무시합니다: {path}")
        return {}

def save_cache(cache: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)

def source_url(row: dict[str, Any]) -> str:
    return first_value(row, SOURCE_URL_COLUMNS)

def infer_source_site(row: dict[str, Any]) -> str:
    explicit = str(row.get("source_site") or row.get("source") or "").lower()
    text = f"{explicit} {source_url(row)} {row.get('_source_file', '')}".lower()
    if "catch" in text:
        return "catch"
    if "jobkorea" in text or "잡코리아" in text:
        return "jobkorea"
    if "incruit" in text or "인크루트" in text or "incru.it" in text:
        return "incruit"
    return explicit


def freelancer_only_evidence(row: dict[str, Any]) -> str:
    """잡코리아 원문에서 프리랜서 단독 공고를 LLM 호출 전에 판별한다."""
    detail = str(row.get("detail") or "")
    etc_info = str(row.get("etc_info") or "")
    match = re.search(
        r"고용형태\s+(.+?)(?=\s{2,}(?:급여|근무시간|근무지주소|인근지하철)|$)",
        detail,
    )
    evidence = match.group(1).strip() if match else etc_info
    if "프리랜서" not in evidence:
        return ""
    if any(employment_type in evidence for employment_type in NON_FREELANCE_TYPES):
        return ""
    return evidence


def excluded_record(row: dict[str, Any], reason: str, evidence: str) -> dict[str, Any]:
    return {
        "company_name": first_value(row, COMPANY_COLUMNS),
        "posting_title": first_value(row, TITLE_COLUMNS),
        "source_site": infer_source_site(row),
        "source_url": source_url(row),
        "source_file": str(row.get("_source_file", "")),
        "source_row": int(row.get("_source_row", 0) or 0),
        "exclusion_reason": reason,
        "original_value": evidence,
    }

def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def snapshot(final_text: str, ocr_text: str) -> dict[str, str]:
    return {
        "raw_ocr_excerpt": ocr_text[:1200],
        "final_text_excerpt": final_text[:1200],
        "llm_input_hash": text_hash(final_text),
    }

def empty_track() -> dict[str, list[str]]:
    return {"requirements": [], "preferred": [], "responsibilities": [], "documents": []}

def empty_record(
    row: dict[str, Any],
    ocr_used: bool,
    final_text: str,
    ocr_text: str,
    error: str,
) -> dict[str, Any]:
    url = source_url(row)
    return {
        "company_name": first_value(row, COMPANY_COLUMNS),
        "posting_title": first_value(row, TITLE_COLUMNS),
        "source_site": infer_source_site(row),
        "source_url": url,
        "common": {"education": "", "major": "", "preferred": [], "documents": []},
        "jobs": [],
        "process": [],
        "work_conditions": {
            "employment_type": "", "work_type": "", "salary": "",
            "benefits": [], "deadline": extract_deadline(final_text),
            "recruit_url": url,
        },
        "raw_meta": runtime_meta(row, ocr_used, False, error),
        "preprocess_log": {
            "dropped_fields": [],
            "low_confidence": [],
            "parse_warnings": [error] if error else [],
            "original_text_snapshot": snapshot(final_text, ocr_text),
        },
    }

def runtime_meta(row: dict[str, Any], ocr_used: bool, llm_used: bool, error: str) -> dict[str, Any]:
    return {
        "source_file": str(row.get("_source_file", "")),
        "source_row": int(row.get("_source_row", 0) or 0),
        "source_url": source_url(row),
        "ocr_used": ocr_used,
        "llm_used": llm_used,
        "llm_error": error,
    }

def extract_deadline(text: str) -> str:
    matches = re.findall(r"(20\d{2})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})", text)
    if not matches:
        return ""
    year, month, day = matches[-1]
    return f"{year}-{int(month):02d}-{int(day):02d}"

def normalize_headcount(value: Any) -> int | None:
    raw = str(value or "").strip()
    if not raw or re.fullmatch(r"0+명?", raw):
        return None
    match = re.search(r"\d+", raw.replace(",", ""))
    return int(match.group()) if match else None

def add_warning(record: dict[str, Any], message: str) -> None:
    warnings = record.setdefault("preprocess_log", {}).setdefault("parse_warnings", [])
    if message and message not in warnings:
        warnings.append(message)

def tracks_are_identical(tracks: dict[str, Any]) -> bool:
    newcomer = tracks.get("newcomer", {})
    experienced = tracks.get("experienced", {})
    keys = ("requirements", "preferred", "responsibilities")
    return all(newcomer.get(key) == experienced.get(key) for key in keys) and any(
        newcomer.get(key) for key in keys
    )

def postprocess_record(
    record: dict[str, Any],
    row: dict[str, Any],
    final_text: str,
    ocr_text: str,
    ocr_used: bool,
    llm_used: bool,
    error: str = "",
) -> dict[str, Any]:
    url = source_url(row)
    record["company_name"] = record.get("company_name") or first_value(row, COMPANY_COLUMNS)
    record["posting_title"] = record.get("posting_title") or first_value(row, TITLE_COLUMNS)
    record["source_site"] = record.get("source_site") or infer_source_site(row)
    record["source_url"] = url
    record["raw_meta"] = runtime_meta(row, ocr_used, llm_used, error)
    record.setdefault("preprocess_log", {})["original_text_snapshot"] = snapshot(final_text, ocr_text)
    work = record.setdefault("work_conditions", {})
    work["deadline"] = work.get("deadline") or extract_deadline(final_text)
    recruit_url = str(work.get("recruit_url") or "").strip()
    if not re.match(r"^https?://", recruit_url, re.I):
        if recruit_url:
            add_warning(record, f"유효하지 않은 recruit_url을 source_url로 교체함: {recruit_url}")
        work["recruit_url"] = url
    for job in record.get("jobs", []):
        job["headcount_value"] = normalize_headcount(job.get("headcount"))
        if tracks_are_identical(job.get("tracks", {})):
            add_warning(record, f"{job.get('job_name', '')}: 신입/경력 조건 동일 - 원문 구분 확인 필요")
    if repeated_job_content(record.get("jobs", [])):
        add_warning(record, "여러 직무에 동일한 근무지/업무/트랙 정보가 반복됨 - 카테고리 오인식 가능성")
    remove_track_labels_from_preferred(record)
    if sparse_job_content(record.get("jobs", [])):
        add_warning(record, "직무별 핵심 정보가 대부분 비어 있음 - OCR/원문 품질 확인 필요")
    replace_null_scalars(record)
    return record

def llm_cache_key(final_text: str, row: dict[str, Any]) -> str:
    raw = "\n".join([
        SCHEMA_VERSION, PROMPT_VERSION, LLM_SCHEMA_VERSION, OPENAI_MODEL,
        str(OPENAI_TEMPERATURE),
        source_url(row),
        first_value(row, TITLE_COLUMNS), final_text,
    ])
    return text_hash(raw)

def openai_headers() -> dict[str, str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 없습니다.")
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

def extract_response_text(data: dict[str, Any]) -> str:
    if data.get("output_text"):
        return data["output_text"]
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("text"):
                return content["text"]
    raise RuntimeError("OpenAI 응답에서 JSON 텍스트를 찾지 못했습니다.")

def build_prompt(final_text: str, row: dict[str, Any], ocr_used: bool) -> str:
    rules = PROMPT_PATH.read_text(encoding="utf-8").strip()
    return f"""{rules}

[메타]
company_name={first_value(row, COMPANY_COLUMNS)}
posting_title={first_value(row, TITLE_COLUMNS)}
source_site={infer_source_site(row)}
source_url={source_url(row)}
source_file={row.get("_source_file", "")}
source_row={row.get("_source_row", 0)}
ocr_used={ocr_used}
llm_input_hash={text_hash(final_text)}

[채용공고]
{final_text[:MAX_LLM_CHARS]}
""".strip()

def call_openai(
    final_text: str,
    row: dict[str, Any],
    ocr_used: bool,
    stats: dict[str, int],
) -> dict[str, Any]:
    body = {
        "model": OPENAI_MODEL,
        "temperature": OPENAI_TEMPERATURE,
        "input": [
            {
                "role": "system",
                "content": "너는 한국 채용공고 OCR 텍스트를 구조화하는 데이터 전처리기다.",
            },
            {"role": "user", "content": build_prompt(final_text, row, ocr_used)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "hcr_job_posting_v2",
                "schema": LLM_SCHEMA,
                "strict": True,
            }
        },
    }
    last_error = "OpenAI 호출 실패"
    for attempt in range(3):
        stats["api_calls"] += 1
        response = requests.post(OPENAI_URL, headers=openai_headers(), json=body, timeout=120)
        if "invalid_api_key" in response.text:
            raise SystemExit("OPENAI_API_KEY가 올바르지 않습니다.")
        if "insufficient_quota" in response.text:
            raise SystemExit("OpenAI 크레딧 또는 사용 한도가 부족합니다.")
        if response.status_code in {429, 500, 502, 503, 504}:
            last_error = response.text[:500]
            time.sleep(2 ** attempt)
            continue
        response.raise_for_status()
        return json.loads(extract_response_text(response.json()))
    raise RuntimeError(last_error)

def normalize_record(
    final_text: str,
    ocr_text: str,
    row: dict[str, Any],
    ocr_used: bool,
    llm_cache: dict[str, Any],
    allow_api: bool,
    stats: dict[str, int],
) -> dict[str, Any]:
    key = llm_cache_key(final_text, row)
    cached = llm_cache.get(key)
    if isinstance(cached, dict):
        stats["cache_hits"] += 1
        record = json.loads(json.dumps(cached, ensure_ascii=False))
        return postprocess_record(record, row, final_text, ocr_text, ocr_used, True)
    if not final_text.strip():
        stats["skipped"] += 1
        return empty_record(row, ocr_used, final_text, ocr_text, "입력 텍스트 없음")
    if not allow_api:
        stats["skipped"] += 1
        return empty_record(row, ocr_used, final_text, ocr_text, "LLM API 호출 비활성화")
    try:
        record = call_openai(final_text, row, ocr_used, stats)
        llm_cache[key] = record
        return postprocess_record(record, row, final_text, ocr_text, ocr_used, True)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as error:
        message = str(error)
        llm_cache[key] = f"[LLM_ERROR] {message}"
        return empty_record(row, ocr_used, final_text, ocr_text, message)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HCR 채용공고 OCR/LLM 전처리")
    parser.add_argument("--input", type=Path, help="처리할 단일 CSV/TSV/JSON/JSONL 파일")
    parser.add_argument("--output", type=Path, help="출력 JSONL 경로")
    parser.add_argument("--audit-output", type=Path, help="전처리 로그 JSONL 경로")
    parser.add_argument("--limit", type=int, default=0, help="처리할 공고 수. 0은 전체")
    parser.add_argument("--no-llm", action="store_true", help="캐시만 사용하고 API 호출하지 않음")
    return parser.parse_args()

def output_path_for(args: argparse.Namespace) -> Path:
    if args.output:
        return args.output.expanduser()
    if args.input:
        return PROCESSED_DIR / f"{args.input.stem}_normalized_v2.jsonl"
    return DEFAULT_OUT_PATH


def excluded_path_for(out_path: Path) -> Path:
    return out_path.with_name(f"{out_path.stem}_excluded.jsonl")

def main() -> None:
    args = parse_args()
    allow_api = USE_LLM and not args.no_llm
    if allow_api and not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY가 없습니다. .env 파일에 넣어주세요.")
    if args.input and not args.input.exists():
        raise SystemExit(f"입력 파일이 없습니다: {args.input}")
    frames = load_raw_files(input_path=args.input)
    if not frames:
        print(f"처리할 파일이 없습니다: {RAW_DIR}")
        return
    out_path = output_path_for(args)
    audit_path = audit_path_for(out_path, args.audit_output)
    excluded_path = excluded_path_for(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ocr_cache = load_cache(OCR_CACHE_PATH)
    llm_cache = load_cache(LLM_CACHE_PATH)
    page_cache = load_cache(PAGE_CACHE_PATH)
    warning_map = {
        "ocr_excluded": "저품질 OCR을 LLM 입력에서 제외하고 원문 스냅샷에 보존함",
        "ocr_failed_text_fallback": "이미지 OCR 결과가 없어 기존 상세 텍스트만 LLM 입력에 사용함",
        "low_quality_but_rich": "OCR 품질이 낮지만 정보량이 많아 LLM 입력에 포함함",
        "page_fetched": "원문 페이지 HTML을 가져와 입력을 보완함",
        "fallback": "사용 가능한 텍스트 품질이 낮아 수동 검수가 필요함",
    }
    total = 0
    stats = {"api_calls": 0, "cache_hits": 0, "skipped": 0, "excluded": 0}
    try:
        with (
            out_path.open("w", encoding="utf-8") as output,
            audit_path.open("w", encoding="utf-8") as audit_output,
            excluded_path.open("w", encoding="utf-8") as excluded_output,
        ):
            for frame in frames:
                for _, series in tqdm(frame.iterrows(), total=len(frame)):
                    if args.limit > 0 and total >= args.limit:
                        break
                    row = series.to_dict()
                    freelance_evidence = freelancer_only_evidence(row)
                    if freelance_evidence:
                        excluded_output.write(json.dumps(
                            excluded_record(
                                row,
                                "고용형태가 프리랜서로만 구성된 공고",
                                freelance_evidence,
                            ),
                            ensure_ascii=False,
                        ) + "\n")
                        stats["excluded"] += 1
                        total += 1
                        continue
                    detail_text = get_detail_text(row)
                    image_urls = find_image_urls(row)
                    if image_urls:
                        ocr_text, ocr_used = ocr_image_urls(image_urls, ocr_cache)
                        input_mode = "image_ocr_llm"
                    else:
                        ocr_text, ocr_used = "", False
                        input_mode = "html_llm" if has_html_text(row) else "text_llm"
                    ocr_low_quality = is_low_quality_ocr(ocr_text)
                    final_text, text_source = build_final_text(
                        detail_text, ocr_text, ocr_low_quality, bool(image_urls),
                        source_url(row), page_cache,
                    )
                    record = normalize_record(
                        final_text, ocr_text, row, ocr_used, llm_cache,
                        allow_api, stats,
                    )
                    record["raw_meta"]["ocr_low_quality"] = ocr_low_quality
                    record["raw_meta"]["text_source"] = text_source
                    record["raw_meta"]["input_mode"] = input_mode
                    record["raw_meta"]["image_count"] = len(image_urls)
                    if text_source in warning_map:
                        add_warning(record, warning_map[text_source])
                    record["raw_meta"]["review_required"] = bool(
                        ocr_low_quality
                        or not record.get("jobs")
                        or sparse_job_content(record.get("jobs", []))
                        or repeated_job_content(record.get("jobs", []))
                        or record["raw_meta"].get("llm_error")
                    )
                    db_record, audit_record = split_record(record)
                    output.write(json.dumps(db_record, ensure_ascii=False) + "\n")
                    audit_output.write(json.dumps(audit_record, ensure_ascii=False) + "\n")
                    total += 1
                    if total % 5 == 0:
                        save_cache(ocr_cache, OCR_CACHE_PATH)
                        save_cache(llm_cache, LLM_CACHE_PATH)
                        save_cache(page_cache, PAGE_CACHE_PATH)
                if args.limit > 0 and total >= args.limit:
                    break
    finally:
        save_cache(ocr_cache, OCR_CACHE_PATH)
        save_cache(llm_cache, LLM_CACHE_PATH)
        save_cache(page_cache, PAGE_CACHE_PATH)
    print(f"완료: {total}건")
    print(f"OpenAI 실제 요청: {stats['api_calls']}회")
    print(f"LLM 캐시 재사용: {stats['cache_hits']}건")
    print(f"LLM 생략: {stats['skipped']}건")
    print(f"프리랜서 단독 제외: {stats['excluded']}건")
    print(f"결과: {out_path}")
    print(f"전처리 로그: {audit_path}")
    print(f"제외 공고: {excluded_path}")

if __name__ == "__main__":
    main()
