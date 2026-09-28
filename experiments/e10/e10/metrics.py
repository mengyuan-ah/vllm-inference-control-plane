from __future__ import annotations

from collections import Counter
from typing import Any

OUTCOMES = {
    "success_no_retry",
    "success_after_precontent_failover",
    "failed_before_content",
    "failed_or_truncated_after_content",
    "timed_out",
    "rejected_no_serving_replica",
    "rejected_safety_bound",
    "client_cancelled",
}


def counter_delta(before: dict[str, Any], after: dict[str, Any]) -> float | None:
    """Never subtract Prometheus counters across process generations."""
    if before["generation"] != after["generation"]:
        return None
    value = float(after["value"]) - float(before["value"])
    return value if value >= 0 else None


def censored_duration(start: float, event: float | None, window_end: float) -> dict[str, Any]:
    return {
        "seconds": (event if event is not None else window_end) - start,
        "censored": event is None,
    }


def reconcile(rows: list[dict[str, Any]], offered: int) -> dict[str, Any]:
    ids = [row["request_id"] for row in rows]
    outcomes = Counter(row["outcome"] for row in rows)
    unknown = sorted(set(outcomes) - OUTCOMES)
    valid = len(rows) == offered and len(set(ids)) == offered and not unknown
    return {
        "valid": valid,
        "offered": offered,
        "unique": len(set(ids)),
        "outcomes": dict(outcomes),
        "unknown": unknown,
    }


def paired_trace_identity(control: list[dict[str, Any]], fault: list[dict[str, Any]]) -> bool:
    fields = ("request_id", "scheduled_offset_s", "prompt_sha256", "max_tokens", "seed")
    return [tuple(row.get(field) for field in fields) for row in control] == [
        tuple(row.get(field) for field in fields) for row in fault
    ]
