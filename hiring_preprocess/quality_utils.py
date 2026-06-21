"""정규화 결과의 품질 검사와 빈 값 후처리."""

from __future__ import annotations

import json
from typing import Any


EMPTY_TRACK = {
    "requirements": [],
    "preferred": [],
    "responsibilities": [],
    "documents": [],
}


def _add_warning(record: dict[str, Any], message: str) -> None:
    warnings = record.setdefault("preprocess_log", {}).setdefault("parse_warnings", [])
    if message not in warnings:
        warnings.append(message)


def remove_common_preferred_duplicates(record: dict[str, Any]) -> None:
    """우대사항 중복을 실제 적용 범위에 맞춰 한쪽에만 남긴다."""
    common = record.get("common", {})
    common_preferred = common.get("preferred", [])
    jobs = record.get("jobs", [])
    if not common_preferred or not jobs:
        return

    job_values = [
        {str(value).strip() for value in job.get("preferred_common", [])}
        for job in jobs
    ]
    keep_common = []
    moved_to_jobs = []
    for value in common_preferred:
        normalized = str(value).strip()
        matched_jobs = sum(normalized in values for values in job_values)
        if matched_jobs == len(jobs) and len(jobs) > 1:
            keep_common.append(value)
        elif matched_jobs:
            moved_to_jobs.append(value)
        else:
            keep_common.append(value)
    common["preferred"] = keep_common

    common_set = {str(value).strip() for value in keep_common}
    removed_from_jobs = False
    for job in jobs:
        preferred = job.get("preferred_common", [])
        filtered = [value for value in preferred if str(value).strip() not in common_set]
        if len(filtered) != len(preferred):
            job["preferred_common"] = filtered
            removed_from_jobs = True

    if moved_to_jobs:
        _add_warning(
            record,
            "일부 직무에만 존재하는 우대사항을 common.preferred에서 제거하고 "
            "해당 preferred_common에 보존함",
        )
    if removed_from_jobs:
        _add_warning(
            record,
            "모든 직무에 반복된 우대사항을 common.preferred에만 보존함",
        )


def posting_track_scope(row: dict[str, Any]) -> str:
    """원본 메타에서 공고 전체가 신입/경력 전용인지 보수적으로 판별한다."""
    text = " ".join(
        str(row.get(key) or "")
        for key in ("title", "posting_title", "etc_info", "detail")
    )
    compact = text.replace(" ", "")
    mixed_markers = (
        "신입/경력", "신입·경력", "신입및경력", "신입또는경력",
        "경력무관", "신입경력",
    )
    if any(marker in compact for marker in mixed_markers):
        return "mixed"

    has_newcomer = "신입" in text or "채용연계형 인턴" in text
    has_experienced = "경력" in text
    if has_experienced and not has_newcomer:
        return "experienced"
    if has_newcomer and not has_experienced:
        return "newcomer"
    return "unknown"


def clear_inapplicable_tracks(
    record: dict[str, Any],
    row: dict[str, Any],
) -> None:
    """전용 공고에서 반대 트랙에 복제된 LLM 결과를 비운다."""
    scope = posting_track_scope(row)
    if scope not in {"newcomer", "experienced"}:
        return

    target = "experienced" if scope == "newcomer" else "newcomer"
    changed = False
    for job in record.get("jobs", []):
        tracks = job.setdefault("tracks", {})
        current = tracks.get(target, {})
        if any(current.get(key) for key in EMPTY_TRACK):
            changed = True
        tracks[target] = {key: list(value) for key, value in EMPTY_TRACK.items()}

    if changed:
        label = "경력" if scope == "newcomer" else "신입"
        _add_warning(
            record,
            f"{scope} 전용 공고로 판별되어 {label} 트랙의 복제된 내용을 비움",
        )


def repeated_job_content(jobs: list[dict[str, Any]]) -> bool:
    if len(jobs) < 3:
        return False
    signatures = [
        json.dumps(
            {
                "locations": job.get("locations", []),
                "responsibilities": job.get("responsibilities", []),
                "preferred_common": job.get("preferred_common", []),
                "tracks": job.get("tracks", {}),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for job in jobs
    ]
    most_common = max(signatures.count(value) for value in set(signatures))
    return most_common / len(signatures) >= 0.75


def sparse_job_content(jobs: list[dict[str, Any]]) -> bool:
    if not jobs:
        return True
    labels = {"신입", "경력", "신입/경력", "경력무관", "무관"}
    sparse = 0
    for job in jobs:
        tracks = job.get("tracks", {})
        track_items = sum(
            sum(str(value).strip() not in labels for value in items)
            for track in tracks.values()
            for items in track.values()
            if isinstance(items, list)
        )
        useful_fields = sum(bool(job.get(key)) for key in (
            "headcount", "education", "major",
            "responsibilities", "preferred_common",
        ))
        if useful_fields + min(track_items, 2) <= 1:
            sparse += 1
    return sparse / len(jobs) >= 0.5


def remove_track_labels_from_preferred(record: dict[str, Any]) -> None:
    labels = {"신입", "경력", "신입/경력", "경력무관", "무관"}
    for job in record.get("jobs", []):
        preferred = job.get("preferred_common", [])
        removed = [value for value in preferred if value.strip() in labels]
        if not removed:
            continue
        job["preferred_common"] = [
            value for value in preferred if value.strip() not in labels
        ]
        log = record.setdefault("preprocess_log", {})
        warnings = log.setdefault("parse_warnings", [])
        message = (
            f"{job.get('job_name', '')}: 신입/경력 레이블을 "
            "preferred_common에서 제거함"
        )
        if message not in warnings:
            warnings.append(message)


def replace_null_scalars(record: dict[str, Any]) -> None:
    common = record.get("common", {})
    for key in ("education", "major"):
        if common.get(key) is None:
            common[key] = ""

    for job in record.get("jobs", []):
        for key in ("headcount", "education", "major"):
            if job.get(key) is None:
                job[key] = ""

    work = record.get("work_conditions", {})
    for key in ("employment_type", "work_type", "salary", "deadline", "recruit_url"):
        if work.get(key) is None:
            work[key] = ""
