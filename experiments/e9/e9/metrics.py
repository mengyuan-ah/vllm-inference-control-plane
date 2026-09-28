from __future__ import annotations

import math
from collections import Counter
from typing import Any

OFFER_WINDOW_S = 32.0
SLO_TTFT_S = 0.250
SLO_TPOT_S = 0.015
SLO_E2E_S = 2.0


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


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


def outcome_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(row["outcome"] for row in rows)
    slo = sum(is_slo_success(row) for row in rows)
    accepted = counts["accepted_success"]
    return {
        "offered": len(rows),
        "slo_success": slo,
        "successful_slo_miss": accepted - slo,
        "accepted_success": accepted,
        "prediction_rejected": counts["prediction_rejected"],
        "safety_rejected": counts["safety_rejected"],
        "failed": counts["failed"],
        "timed_out": counts["timed_out"],
    }


def summarize(rows: list[dict[str, Any]], *, offered_count: int) -> dict[str, Any]:
    outcomes = outcome_summary(rows)
    accepted = [row for row in rows if row["outcome"] == "accepted_success"]
    decisions = [row.get("admission_decision") for row in rows]
    defined = sum(isinstance(value, bool) for value in decisions)
    scorer_failures = sum(
        isinstance(row.get("decision_reason"), str)
        and row["decision_reason"].startswith("scorer_error:")
        for row in rows
    )
    per_cycle = {}
    first_eight = {}
    for cycle in range(8):
        cycle_rows = [row for row in rows if row.get("cycle_index") == cycle]
        per_cycle[str(cycle)] = outcome_summary(cycle_rows)
        first_eight[str(cycle)] = outcome_summary(
            [row for row in cycle_rows if row.get("cycle_offer_index", 99) < 8]
        )
    completed = [float(row["completed_offset_s"]) for row in rows]
    actual = [float(row["actual_offset_s"]) for row in rows]
    completion_window = max(completed) - min(actual) if completed and actual else None
    payload = {
        "offered_count": offered_count,
        "recorded_count": len(rows),
        "measurement_window_s": OFFER_WINDOW_S,
        "outcomes": {
            key: outcomes[key]
            for key in (
                "accepted_success",
                "prediction_rejected",
                "safety_rejected",
                "failed",
                "timed_out",
            )
        },
        "accepted_count": len(accepted),
        "successful_but_slo_missing_count": outcomes["successful_slo_miss"],
        "slo_success_count": outcomes["slo_success"],
        "slo_goodput_rps": outcomes["slo_success"] / OFFER_WINDOW_S,
        "slo_success_fraction": outcomes["slo_success"] / offered_count,
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
            "defined_count": defined,
            "undefined_count": len(rows) - defined,
            "denominator_count": offered_count,
            "fraction": defined / offered_count,
            "scorer_failure_count": scorer_failures,
        },
        "per_cycle": per_cycle,
        "first_eight_per_cycle": first_eight,
    }
    payload["outcome_reconciliation_ok"] = sum(payload["outcomes"].values()) == offered_count
    return payload


def recovery_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    resolution = max(
        (
            right["offset_s"] - left["offset_s"]
            for left, right in zip(samples, samples[1:], strict=False)
        ),
        default=None,
    )

    def after(offset: float) -> dict[str, Any] | None:
        return next((sample for sample in samples if sample["offset_s"] >= offset), None)

    cycles = []
    for cycle in range(8):
        boundary, next_burst = 4 * cycle + 2, 4 * cycle + 4
        high_end = after(boundary)
        next_sample = after(next_burst)
        drained = next(
            (
                sample
                for sample in samples
                if boundary <= sample["offset_s"] < next_burst
                and sample["gateway"]["outstanding_requests"] == 0
            ),
            None,
        )
        cycles.append(
            {
                "cycle_index": cycle,
                "high_rate_boundary_s": boundary,
                "next_burst_s": next_burst,
                "high_end": high_end,
                "reached_zero_before_next_burst": drained is not None,
                "time_to_zero_s": drained["offset_s"] - boundary if drained else None,
                "recovery_censored": drained is None,
                "residual_at_next_burst": next_sample,
            }
        )
    return {"sampling_max_interval_s": resolution, "cycles": cycles}


def evaluation_reasons(
    rows: list[dict[str, Any]], *, expected_count: int, configuration: str
) -> list[str]:
    reasons = []
    if len(rows) != expected_count:
        reasons.append(f"recorded {len(rows)} of {expected_count} offered requests")
    if len({row.get("request_id") for row in rows}) != len(rows):
        reasons.append("request IDs are not unique")
    if any(not isinstance(row.get("admission_decision"), bool) for row in rows):
        reasons.append("admission-decision coverage is incomplete")
    if any(row["outcome"] in {"failed", "timed_out"} for row in rows):
        reasons.append("unexpected request failure or timeout occurred")
    if any(row["outcome"] == "safety_rejected" for row in rows):
        reasons.append(f"configuration {configuration} safety limit bound")
    return reasons
