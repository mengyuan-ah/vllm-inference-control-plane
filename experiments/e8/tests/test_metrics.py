from __future__ import annotations

from e8.metrics import classify, evaluation_reasons, percentile, summarize


def row(outcome: str, decision: bool | None = False, offset: float = 0.0) -> dict:
    return {
        "outcome": outcome,
        "admission_decision": decision,
        "decision_reason": "count_threshold",
        "actual_offset_s": offset,
        "completed_offset_s": offset + 1.0,
        "ttft_s": 0.1,
        "tpot_s": 0.01,
        "e2e_s": 1.0,
    }


def test_outcome_buckets_are_distinct() -> None:
    assert classify(429, "prediction_count_threshold", False, False) == "prediction_rejected"
    assert classify(429, "queue_request_limit", False, False) == "safety_rejected"
    assert classify(0, None, False, True) == "timed_out"
    assert classify(500, None, False, False) == "failed"


def test_goodput_uses_fixed_offer_window_and_reconciles() -> None:
    rows = [row("accepted_success", offset=0.0), row("prediction_rejected", True, 0.1)]
    result = summarize(rows, offered_count=2, rate=10)
    assert result["slo_success_count"] == 1
    assert result["slo_goodput_rps"] == 5.0
    assert result["outcome_reconciliation_ok"] is True
    assert result["accepted_count"] == 1
    assert result["decision_coverage"]["fraction"] == 1.0


def test_undefined_decision_invalidates_evaluation() -> None:
    rows = [row("accepted_success", None)]
    assert "admission-decision coverage is incomplete" in evaluation_reasons(
        rows, expected_count=1, configuration="B"
    )


def test_percentile_supports_arrival_lag_gate() -> None:
    assert percentile([0.0, 0.01, 0.02, 0.03, 0.3], 0.95) > 0.05
