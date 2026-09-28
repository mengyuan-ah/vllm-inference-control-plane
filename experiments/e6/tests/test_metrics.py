from __future__ import annotations

import pytest

from e6.metrics import (
    classify,
    clean_response_success,
    is_slo_success,
    validate_accounting,
    validity_reasons,
)


def accounting() -> dict:
    return {
        "queue": {"queued_requests": 0, "queued_work": 0.0},
        "replicas": {
            "replica1": {"inflight": 0, "active_reservations": 0, "outstanding_work": 0},
            "replica2": {"inflight": 0, "active_reservations": 0, "outstanding_work": 0},
        },
    }


def test_expected_rejections_are_not_infrastructure_failures() -> None:
    assert classify(429, "queue_request_limit", False) == "overload_rejection"
    assert classify(429, "queue_timeout", False) == "queue_expiration"
    assert classify(503, "no_healthy_replica", False) == "unexpected_failure"


def test_slo_requires_exact_success_and_all_thresholds() -> None:
    row = {"success": True, "ttft_s": 0.250, "tpot_s": 0.015, "e2e_s": 2.0}
    assert is_slo_success(row)
    row["e2e_s"] = 2.001
    assert not is_slo_success(row)
    row.update(success=False, e2e_s=1.0)
    assert not is_slo_success(row)


def test_final_accounting_must_be_empty() -> None:
    assert validate_accounting(accounting()) == []
    leaked = accounting()
    leaked["replicas"]["replica1"]["active_reservations"] = 1
    assert validate_accounting(leaked)


@pytest.mark.parametrize(
    "override",
    [
        {"error": "ClientPayloadError: broken stream"},
        {"saw_sse_error": True},
        {"saw_done": False},
    ],
)
def test_broken_or_incomplete_stream_is_not_success(override: dict) -> None:
    fields = {
        "status": 200,
        "error": "",
        "saw_sse_error": False,
        "saw_done": True,
        "saw_first_content": True,
        "usage": {"prompt_tokens": 1024, "completion_tokens": 128},
        "finish_reason": "length",
    }
    fields.update(override)
    assert not clean_response_success(**fields)


def record(*, outcome: str = "success", lag: float = 0.001) -> dict:
    return {"outcome": outcome, "arrival_lag_s": lag}


def validity(**overrides) -> list[str]:
    values = {
        "configuration": "B",
        "records": [record()],
        "events": {},
        "accounting": accounting(),
        "metric_deltas": {},
        "expected_count": 1,
        "execution_error": None,
    }
    values.update(overrides)
    return validity_reasons(**values)


def test_arrival_lag_and_failover_invalidate_arm() -> None:
    reasons = validity(
        records=[record(lag=0.3)],
        events={"r1": [{"event": "failover"}]},
    )
    assert "arrival lag p95 exceeded 50 ms" in reasons
    assert "single arrival lag exceeded 250 ms" in reasons
    assert "failover occurred" in reasons


def test_configuration_a_rejection_invalidates_nonbinding_claim() -> None:
    reasons = validity(configuration="A", records=[record(outcome="overload_rejection")])
    assert "configuration A safety cap bound" in reasons


def test_partial_evidence_and_execution_error_invalidate_arm() -> None:
    reasons = validity(records=[], execution_error="TimeoutError", expected_count=64)
    assert any("execution error" in reason for reason in reasons)
    assert any("recorded 0 of 64" in reason for reason in reasons)
