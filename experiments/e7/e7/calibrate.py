from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _score(rows: list[dict[str, Any]], feature: str, threshold: float) -> tuple[float, int]:
    positives = [row for row in rows if row["slo_miss"]]
    negatives = [row for row in rows if not row["slo_miss"]]
    tp = sum(float(row[feature]) >= threshold for row in positives)
    tn = sum(float(row[feature]) < threshold for row in negatives)
    fp = len(negatives) - tn
    return ((tp / len(positives)) + (tn / len(negatives))) / 2, fp


def choose_threshold(rows: list[dict[str, Any]], feature: str) -> float:
    valid = [row for row in rows if row["slo_miss"] is not None and row.get(feature) is not None]
    if not any(row["slo_miss"] for row in valid) or not any(not row["slo_miss"] for row in valid):
        raise ValueError("calibration requires both SLO passes and misses")
    candidates = sorted({float(row[feature]) for row in valid})
    return max(
        candidates,
        key=lambda value: (
            _score(valid, feature, value)[0],
            -_score(valid, feature, value)[1],
            -value,
        ),
    )


def fit(arm_dirs: list[Path]) -> dict[str, Any]:
    if len({path.name for path in arm_dirs}) != len(arm_dirs):
        raise ValueError("calibration inputs must be distinct whole arms")
    rows: list[dict[str, Any]] = []
    for path in arm_dirs:
        summary = json.loads((path / "summary.json").read_text())
        if (
            summary["stage"] != "calibration"
            or not summary["infrastructure_valid"]
            or not summary.get("prediction_capture_complete", True)
        ):
            raise ValueError(f"not a valid calibration arm: {path}")
        rows.extend(json.loads((path / "requests.json").read_text()))
    return {
        "predictor_version": "e7-shadow-v1",
        "count_threshold": int(choose_threshold(rows, "selected_inflight_before")),
        "work_threshold": choose_threshold(rows, "selected_outstanding_work_before"),
        "selection_rule": (
            "maximize calibration balanced accuracy; tie-break by fewer false positives, "
            "then lower threshold"
        ),
        "calibration_arms": [path.name for path in arm_dirs],
        "calibration_rows": len(rows),
    }
