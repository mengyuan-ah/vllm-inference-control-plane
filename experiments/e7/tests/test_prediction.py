from __future__ import annotations

import json
from pathlib import Path

import pytest

from e7.calibrate import choose_threshold, fit
from e7.metrics import (
    clean_response_success,
    confusion,
    label_outcome,
    prediction_capture_reasons,
    prediction_evaluation_reasons,
    validity_reasons,
)


def test_outcome_boundaries_and_failures() -> None:
    passing = label_outcome({"success": True, "ttft_s": 0.250, "tpot_s": 0.015, "e2e_s": 2.0})
    assert passing == {"ttft_miss": False, "tpot_miss": False, "e2e_miss": False, "slo_miss": False}
    assert (
        label_outcome({"success": True, "ttft_s": 0.251, "tpot_s": 0.015, "e2e_s": 2.0})["slo_miss"]
        is True
    )
    assert label_outcome({"success": False})["slo_miss"] is None


def test_confusion_marks_zero_denominators_undefined() -> None:
    rows = [{"slo_miss": False, "predictions": {"always_pass": False}}]
    result = confusion(rows, "always_pass")
    assert result["precision"] is None and result["recall"] is None
    assert result["false_positive_rate"] == 0.0
    assert result["evaluated_count"] == 1
    assert result["undefined_count"] == 0
    assert result["coverage"] == 1.0
    assert result["coverage_denominator_count"] == 1


def test_confusion_reports_undefined_prediction_coverage() -> None:
    rows = [
        {"slo_miss": True, "predictions": {"count_threshold": True}},
        {
            "slo_miss": False,
            "predictions": {"count_threshold": None},
            "prediction_reasons": {"count_threshold": "stale_features"},
        },
        {"slo_miss": None, "predictions": {"count_threshold": True}},
    ]
    result = confusion(rows, "count_threshold")
    assert result["total_request_count"] == 3
    assert result["eligible_count"] == 2
    assert result["defined_outcome_count"] == 2
    assert result["defined_prediction_count"] == 2
    assert result["evaluated_count"] == 1
    assert result["undefined_count"] == 1
    assert result["excluded_count"] == 2
    assert result["exclusion_reasons"] == {"stale_features": 1, "undefined_outcome": 1}
    assert result["coverage"] == pytest.approx(1 / 3)


def test_clean_stream_and_failure_classification() -> None:
    fields = {
        "status": 200,
        "error": "",
        "saw_sse_error": False,
        "saw_done": True,
        "saw_first_content": True,
        "usage": {"prompt_tokens": 1024, "completion_tokens": 128},
        "finish_reason": "length",
    }
    assert clean_response_success(**fields)
    fields["saw_done"] = False
    assert not clean_response_success(**fields)


def test_timeout_partial_evidence_is_invalid_but_preserved() -> None:
    accounting = {"queue": {"queued_requests": 0, "queued_work": 0}, "replicas": {}}
    reasons = validity_reasons(
        records=[],
        events={},
        accounting=accounting,
        metric_deltas={},
        expected_count=96,
        execution_error="TimeoutError",
    )
    assert any("recorded 0 of 96" in reason for reason in reasons)
    assert any("TimeoutError" in reason for reason in reasons)


def test_measured_comparison_requires_complete_prediction_coverage() -> None:
    record = {
        "request_id": "one",
        "success": True,
        "arrival_lag_s": 0.0,
        "slo_miss": False,
        "predictions": {"count_threshold": None},
    }
    reasons = prediction_evaluation_reasons(
        records=[record],
        expected_count=1,
        predictor_names=("count_threshold",),
    )
    assert any("prediction coverage incomplete" in reason for reason in reasons)
    assert any("confusion coverage incomplete" in reason for reason in reasons)


def test_complete_measured_prediction_population_is_valid() -> None:
    records = [
        {
            "request_id": "one",
            "slo_miss": False,
            "predictions": {"count_threshold": False, "work_threshold": False},
        },
        {
            "request_id": "two",
            "slo_miss": True,
            "predictions": {"count_threshold": True, "work_threshold": True},
        },
    ]
    assert not prediction_evaluation_reasons(
        records=records,
        expected_count=2,
        predictor_names=("count_threshold", "work_threshold"),
    )


def test_absent_prediction_event_is_evaluation_incomplete() -> None:
    reasons = prediction_capture_reasons(
        records=[
            {
                "request_id": "one",
                "slo_miss": False,
                "predictions": {"count_threshold": False},
            }
        ],
        events={},
        expected_count=1,
    )
    assert reasons == ["one lacks exactly one shadow prediction event"]


def test_malformed_prediction_is_reported_and_rejected() -> None:
    row = {
        "request_id": "one",
        "slo_miss": False,
        "predictions": {"count_threshold": 0},
    }
    coverage = confusion([row], "count_threshold")
    assert coverage["defined_prediction_count"] == 0
    assert coverage["exclusion_reasons"] == {"malformed_prediction": 1}
    reasons = prediction_evaluation_reasons(
        records=[row],
        expected_count=1,
        predictor_names=("count_threshold",),
    )
    assert len(reasons) == 2


def test_calibration_without_fitted_thresholds_remains_service_valid() -> None:
    reasons = validity_reasons(
        records=[{"success": True, "arrival_lag_s": 0.0, "predictions": {}}],
        events={"one": [{"event": "shadow_prediction"}]},
        accounting={"queue": {"queued_requests": 0, "queued_work": 0}, "replicas": {}},
        metric_deltas={},
        expected_count=1,
        execution_error=None,
    )
    assert reasons == []


def test_threshold_selection_requires_mixed_labels() -> None:
    with pytest.raises(ValueError, match="both SLO passes and misses"):
        choose_threshold([{"slo_miss": False, "feature": 1}], "feature")


def test_calibration_accepts_only_distinct_calibration_arms(tmp_path: Path) -> None:
    arm = tmp_path / "arm-one"
    arm.mkdir()
    (arm / "summary.json").write_text(
        json.dumps({"stage": "calibration", "infrastructure_valid": True})
    )
    rows = [
        {"slo_miss": False, "selected_inflight_before": 0, "selected_outstanding_work_before": 0},
        {"slo_miss": True, "selected_inflight_before": 2, "selected_outstanding_work_before": 128},
    ]
    (arm / "requests.json").write_text(json.dumps(rows))
    result = fit([arm])
    assert result["count_threshold"] == 2
    with pytest.raises(ValueError, match="distinct whole arms"):
        fit([arm, arm])
