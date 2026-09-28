from __future__ import annotations

from e9.metrics import evaluation_reasons, recovery_summary, summarize


def row(index: int, outcome: str, *, decision: bool = False) -> dict:
    accepted = outcome == "accepted_success"
    return {
        "request_id": f"r-{index}",
        "outcome": outcome,
        "admission_decision": decision,
        "decision_reason": None,
        "actual_offset_s": index / 8,
        "completed_offset_s": index / 8 + 1,
        "ttft_s": 0.1 if accepted else None,
        "tpot_s": 0.01 if accepted else None,
        "e2e_s": 1.0 if accepted else None,
        "cycle_index": index // 32,
        "cycle_offer_index": index % 32,
    }


def test_goodput_uses_fixed_window_and_rejections_stay_out_of_latency() -> None:
    rows = [row(0, "accepted_success"), row(1, "prediction_rejected", decision=True)]
    result = summarize(rows, offered_count=2)
    assert result["measurement_window_s"] == 32
    assert result["slo_success_count"] == 1
    assert result["slo_goodput_rps"] == 1 / 32
    assert result["slo_success_fraction"] == 0.5
    assert result["accepted_latency_s"]["ttft_s"]["p50"] == 0.1
    assert result["outcome_reconciliation_ok"] is True


def test_cycle_cohorts_and_recovery_censoring() -> None:
    rows = [row(index, "accepted_success") for index in range(32)]
    result = summarize(rows, offered_count=32)
    assert result["per_cycle"]["0"]["offered"] == 32
    assert result["first_eight_per_cycle"]["0"]["offered"] == 8
    samples = [
        {
            "offset_s": value,
            "gateway": {
                "outstanding_requests": 1 if value < 3 else 0,
                "outstanding_work": 1.0 if value < 3 else 0.0,
                "queue_depth": 0,
                "queue_work": 0.0,
            },
            "backends": {},
        }
        for value in (0.0, 2.0, 3.0, 4.0, 6.0, 8.0, 32.0)
    ]
    recovery = recovery_summary(samples)
    assert recovery["cycles"][0]["reached_zero_before_next_burst"] is True
    assert recovery["cycles"][0]["time_to_zero_s"] == 1.0
    assert recovery["cycles"][1]["recovery_censored"] is False


def test_incomplete_decisions_and_duplicate_ids_invalidate() -> None:
    rows = [row(0, "accepted_success"), row(0, "accepted_success")]
    rows[0]["admission_decision"] = None
    reasons = evaluation_reasons(rows, expected_count=2, configuration="A")
    assert "admission-decision coverage is incomplete" in reasons
    assert "request IDs are not unique" in reasons
