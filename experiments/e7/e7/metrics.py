from __future__ import annotations

import math
from typing import Any

SLO_TTFT_S = 0.250
SLO_TPOT_S = 0.015
SLO_E2E_S = 2.0


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


def label_outcome(row: dict[str, Any]) -> dict[str, bool | None]:
    if not row["success"]:
        return {"ttft_miss": None, "tpot_miss": None, "e2e_miss": None, "slo_miss": None}
    ttft = float(row["ttft_s"]) > SLO_TTFT_S
    tpot = float(row["tpot_s"]) > SLO_TPOT_S
    e2e = float(row["e2e_s"]) > SLO_E2E_S
    return {"ttft_miss": ttft, "tpot_miss": tpot, "e2e_miss": e2e, "slo_miss": ttft or tpot or e2e}


def safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def confusion(rows: list[dict[str, Any]], predictor: str) -> dict[str, Any]:
    total = len(rows)
    defined_outcomes = sum(isinstance(row.get("slo_miss"), bool) for row in rows)
    defined_predictions = sum(
        isinstance(row.get("predictions"), dict)
        and isinstance(row["predictions"].get(predictor), bool)
        for row in rows
    )
    evaluated: list[dict[str, Any]] = []
    exclusion_reasons: dict[str, int] = {}
    for row in rows:
        reason: str | None = None
        if not isinstance(row.get("slo_miss"), bool):
            reason = "undefined_outcome"
        elif not isinstance(row.get("predictions"), dict):
            reason = "missing_prediction_map"
        elif predictor not in row["predictions"]:
            reason = "missing_prediction"
        elif row["predictions"][predictor] is None:
            prediction_reasons = row.get("prediction_reasons")
            reason = (
                prediction_reasons.get(predictor)
                if isinstance(prediction_reasons, dict)
                and isinstance(prediction_reasons.get(predictor), str)
                else "undefined_prediction"
            )
        elif not isinstance(row["predictions"][predictor], bool):
            reason = "malformed_prediction"
        else:
            evaluated.append(row)
        if reason is not None:
            exclusion_reasons[reason] = exclusion_reasons.get(reason, 0) + 1
    outcomes = [(row["predictions"][predictor], row["slo_miss"]) for row in evaluated]
    tp = sum(prediction is True and actual is True for prediction, actual in outcomes)
    fp = sum(prediction is True and actual is False for prediction, actual in outcomes)
    fn = sum(prediction is False and actual is True for prediction, actual in outcomes)
    tn = sum(prediction is False and actual is False for prediction, actual in outcomes)
    return {
        "total_request_count": total,
        "defined_outcome_count": defined_outcomes,
        "defined_prediction_count": defined_predictions,
        "eligible_count": defined_outcomes,
        "evaluated_count": len(evaluated),
        "undefined_count": total - defined_predictions,
        "excluded_count": total - len(evaluated),
        "exclusion_reasons": exclusion_reasons,
        "coverage_numerator_count": len(evaluated),
        "coverage_denominator_count": total,
        "coverage": safe_ratio(len(evaluated), total),
        "predicted_miss_actual_miss": tp,
        "predicted_miss_actual_pass": fp,
        "predicted_pass_actual_miss": fn,
        "predicted_pass_actual_pass": tn,
        "precision": safe_ratio(tp, tp + fp),
        "recall": safe_ratio(tp, tp + fn),
        "false_positive_rate": safe_ratio(fp, fp + tn),
    }


def validate_accounting(accounting: dict[str, Any], tolerance: float = 1e-6) -> list[str]:
    reasons: list[str] = []
    queue = accounting["queue"]
    if queue["queued_requests"] != 0 or abs(float(queue["queued_work"])) > tolerance:
        reasons.append("gateway queue did not drain")
    for replica_id, replica in accounting["replicas"].items():
        if replica["inflight"] != 0 or replica.get("active_reservations", 0) != 0:
            reasons.append(f"{replica_id} reservation ledger did not drain")
        if (
            not math.isfinite(float(replica["outstanding_work"]))
            or abs(float(replica["outstanding_work"])) > tolerance
        ):
            reasons.append(f"{replica_id} work did not drain")
    return reasons


def validity_reasons(
    *,
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
    if any(not row["success"] for row in records):
        reasons.append("unexpected request failures occurred")
    lags = [float(row["arrival_lag_s"]) for row in records]
    if lags and percentile(lags, 0.95) > 0.050:
        reasons.append("arrival lag p95 exceeded 50 ms")
    if lags and max(lags) > 0.250:
        reasons.append("single arrival lag exceeded 250 ms")
    if any(event.get("event") == "failover" for items in events.values() for event in items):
        reasons.append("failover occurred")
    if any(value != 0 for metrics in metric_deltas.values() for value in metrics.values()):
        reasons.append("preemption or prefix-cache hit occurred")
    for request_id, items in events.items():
        if (
            any(event.get("event") == "reservation" for event in items)
            and sum(event.get("event") == "final_release" for event in items) != 1
        ):
            reasons.append(f"{request_id} lacks exactly one final release")
    return reasons


def prediction_capture_reasons(
    *,
    records: list[dict[str, Any]],
    events: dict[str, list[dict[str, Any]]],
    expected_count: int,
) -> list[str]:
    """Validate shadow-event capture without requiring fitted predictor decisions."""
    reasons: list[str] = []
    if len(records) != expected_count:
        reasons.append(
            f"prediction capture population incomplete: recorded {len(records)} of "
            f"{expected_count} requests"
        )
    for row in records:
        request_id = row.get("request_id")
        if not isinstance(request_id, str):
            reasons.append("request row lacks a valid request_id for prediction-event matching")
            continue
        prediction_events = sum(
            event.get("event") == "shadow_prediction" for event in events.get(request_id, [])
        )
        if prediction_events != 1:
            reasons.append(f"{request_id} lacks exactly one shadow prediction event")
    return reasons


def prediction_evaluation_reasons(
    *,
    records: list[dict[str, Any]],
    expected_count: int,
    predictor_names: tuple[str, ...],
) -> list[str]:
    """Validate measured-comparison completeness separately from service validity."""
    reasons: list[str] = []
    if len(records) != expected_count:
        reasons.append(
            f"evaluation population incomplete: recorded {len(records)} of "
            f"{expected_count} requests"
        )
    defined_outcomes = sum(isinstance(row.get("slo_miss"), bool) for row in records)
    if defined_outcomes != expected_count:
        reasons.append(
            f"outcome coverage incomplete: defined {defined_outcomes} of {expected_count} requests"
        )
    for predictor in predictor_names:
        coverage = confusion(records, predictor)
        if coverage["defined_prediction_count"] != expected_count:
            reasons.append(
                f"{predictor} prediction coverage incomplete: defined "
                f"{coverage['defined_prediction_count']} of {expected_count} requests"
            )
        if coverage["evaluated_count"] != expected_count:
            reasons.append(
                f"{predictor} confusion coverage incomplete: included "
                f"{coverage['evaluated_count']} of {expected_count} requests"
            )
    return reasons
