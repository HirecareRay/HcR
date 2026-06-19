"""정규화 결과의 품질 검사와 빈 값 후처리."""

from __future__ import annotations

import json
from typing import Any


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
