"""DB 적재 데이터와 전처리 감사 로그 분리."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def audit_path_for(out_path: Path, requested: Path | None) -> Path:
    path = (
        requested.expanduser()
        if requested
        else out_path.with_name(f"{out_path.stem}_preprocess_audit.jsonl")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def split_record(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    db_record = dict(record)
    preprocess_log = db_record.pop("preprocess_log", {})
    raw_meta = record.get("raw_meta", {})
    snapshot = preprocess_log.get("original_text_snapshot", {})

    audit_record = {
        "company_name": record.get("company_name", ""),
        "posting_title": record.get("posting_title", ""),
        "source_site": record.get("source_site", ""),
        "source_url": record.get("source_url", ""),
        "source_file": raw_meta.get("source_file", ""),
        "source_row": raw_meta.get("source_row", 0),
        "review_required": raw_meta.get("review_required", False),
        "llm_input_hash": snapshot.get("llm_input_hash", ""),
        "preprocess_log": preprocess_log,
    }
    return db_record, audit_record
