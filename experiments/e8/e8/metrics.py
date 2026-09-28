from __future__ import annotations

import math
from collections import Counter
from typing import Any

SLO_TTFT_S = 0.250
SLO_TPOT_S = 0.015
SLO_E2E_S = 2.0


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return (
        ordered[low]
        if low == high
        else ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    )


def classify(status: int, error_code: str | None, success: bool, timed_out: bool) -> str:
    if success:
        return "accepted_success"
    if status == 429 and error_code == "prediction_count_threshold":
        return "prediction_rejected"
    if status in {429, 503} and error_code in {
        "queue_request_limit",
        "queue_work_limit",
        "queue_timeout",
        "no_healthy_replica",
        "replicas_unavailable",
    }:
        return "safety_rejected"
    if timed_out:
        return "timed_out"
    return "failed"


def is_slo_success(row: dict[str, Any]) -> bool:
    return bool(
        row["outcome"] == "accepted_success"
        and row["ttft_s"] <= SLO_TTFT_S
        and row["tpot_s"] <= SLO_TPOT_S
        and row["e2e_s"] <= SLO_E2E_S
    )


def summarize(rows: list[dict[str, Any]], *, offered_count: int, rate: float) -> dict[str, Any]:
    measurement_window = offered_count / rate
    outcomes = Counter(row["outcome"] for row in rows)
    accepted = [row for row in rows if row["outcome"] == "accepted_success"]
    slo_successes = sum(is_slo_success(row) for row in rows)
    completed_offsets = [float(row["completed_offset_s"]) for row in rows]
    completion_window = (
        max(completed_offsets) - min(float(row["actual_offset_s"]) for row in rows)
        if completed_offsets
        else None
    )
    decisions = [row.get("admission_decision") for row in rows]
    defined_decisions = sum(isinstance(decision, bool) for decision in decisions)
    scorer_failures = sum(
        isinstance(row.get("decision_reason"), str)
        and row["decision_reason"].startswith("scorer_error:")
        for row in rows
    )
    payload = {
        "offered_count": offered_count,
        "recorded_count": len(rows),
        "measurement_window_s": measurement_window,
        "outcomes": {
            name: outcomes[name]
            for name in (
                "accepted_success",
                "prediction_rejected",
                "safety_rejected",
                "failed",
                "timed_out",
            )
        },
        "accepted_count": len(accepted),
        "successful_but_slo_missing_count": len(accepted) - slo_successes,
        "slo_success_count": slo_successes,
        "slo_goodput_rps": slo_successes / measurement_window,
        "slo_success_fraction": slo_successes / offered_count,
        "accepted_latency_s": {
            metric: {
                "p50": percentile([float(row[metric]) for row in accepted], 0.5),
                "p95": percentile([float(row[metric]) for row in accepted], 0.95),
            }
            for metric in ("ttft_s", "tpot_s", "e2e_s")
        },
        "completion_observation_window_s": completion_window,
        "completion_throughput_rps": (
            len(accepted) / completion_window
            if completion_window and completion_window > 0
            else None
        ),
        "decision_coverage": {
            "defined_count": defined_decisions,
            "undefined_count": len(rows) - defined_decisions,
            "denominator_count": offered_count,
            "fraction": defined_decisions / offered_count,
            "scorer_failure_count": scorer_failures,
        },
    }
    payload["outcome_reconciliation_ok"] = sum(payload["outcomes"].values()) == offered_count
    return payload


def evaluation_reasons(
    rows: list[dict[str, Any]], *, expected_count: int, configuration: str
) -> list[str]:
    reasons: list[str] = []
    if len(rows) != expected_count:
        reasons.append(f"recorded {len(rows)} of {expected_count} offered requests")
    if any(not isinstance(row.get("admission_decision"), bool) for row in rows):
        reasons.append("admission-decision coverage is incomplete")
    if any(row["outcome"] in {"failed", "timed_out"} for row in rows):
        reasons.append("unexpected request failure or timeout occurred")
    if any(row["outcome"] == "safety_rejected" for row in rows):
        reasons.append(f"configuration {configuration} safety limit bound")
    return reasons
