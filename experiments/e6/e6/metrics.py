from __future__ import annotations

import math
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
    return ordered[low] if low == high else ordered[low] + (
        ordered[high] - ordered[low]
    ) * (position - low)


def is_slo_success(row: dict[str, Any]) -> bool:
    return bool(
        row["success"]
        and row["ttft_s"] <= SLO_TTFT_S
        and row["tpot_s"] <= SLO_TPOT_S
        and row["e2e_s"] <= SLO_E2E_S
    )


def classify(status: int, error_code: str | None, success: bool) -> str:
    if success:
        return "success"
    if status == 429 and error_code in {"queue_request_limit", "queue_work_limit"}:
        return "overload_rejection"
    if status == 429 and error_code == "queue_timeout":
        return "queue_expiration"
    return "unexpected_failure"


def clean_response_success(
    *,
    status: int,
    error: str,
    saw_sse_error: bool,
    saw_done: bool,
    saw_first_content: bool,
    usage: dict[str, Any] | None,
    finish_reason: str | None,
) -> bool:
    return bool(
        status == 200
        and not error
        and not saw_sse_error
        and saw_done
        and saw_first_content
        and usage
        and usage.get("prompt_tokens") == 1024
        and usage.get("completion_tokens") == 128
        and finish_reason == "length"
    )


def validate_accounting(accounting: dict[str, Any], tolerance: float = 1e-6) -> list[str]:
    reasons: list[str] = []
    queue = accounting["queue"]
    if queue["queued_requests"] != 0 or abs(float(queue["queued_work"])) > tolerance:
        reasons.append("gateway queue did not drain")
    for replica_id, replica in accounting["replicas"].items():
        if replica["inflight"] != 0 or replica.get("active_reservations", 0) != 0:
            reasons.append(f"{replica_id} reservation ledger did not drain")
        if not math.isfinite(float(replica["outstanding_work"])) or abs(
            float(replica["outstanding_work"])
        ) > tolerance:
            reasons.append(f"{replica_id} work did not drain")
    return reasons


def validity_reasons(
    *,
    configuration: str,
    records: list[dict[str, Any]],
    events: dict[str, list[dict[str, Any]]],
    accounting: dict[str, Any],
    metric_deltas: dict[str, dict[str, float]],
    expected_count: int,
    execution_error: str | None,
) -> list[str]:
    reasons = validate_accounting(accounting)
    if execution_error:
        reasons.append(f"arm execution error: {execution_error}")
    if len(records) != expected_count:
        reasons.append(f"partial request evidence: recorded {len(records)} of {expected_count}")
    if any(row["outcome"] == "unexpected_failure" for row in records):
        reasons.append("unexpected request failures occurred")
    lags = [float(row["arrival_lag_s"]) for row in records]
    if lags and percentile(lags, 0.95) > 0.050:
        reasons.append("arrival lag p95 exceeded 50 ms")
    if lags and max(lags) > 0.250:
        reasons.append("single arrival lag exceeded 250 ms")
    failovers = sum(
        event.get("event") == "failover"
        for request_events in events.values()
        for event in request_events
    )
    if failovers:
        reasons.append("failover occurred")
    if configuration == "A" and any(
        row["outcome"] in {"overload_rejection", "queue_expiration"} for row in records
    ):
        reasons.append("configuration A safety cap bound")
    if any(value != 0 for metrics in metric_deltas.values() for value in metrics.values()):
        reasons.append("preemption or prefix-cache hit occurred")
    for request_id, request_events in events.items():
        if any(event.get("event") == "reservation" for event in request_events) and sum(
            event.get("event") == "final_release" for event in request_events
        ) != 1:
            reasons.append(f"{request_id} lacks exactly one final release")
    return reasons
