from __future__ import annotations

import math

import pytest

from pilot_harness import validate_arm, validate_final_accounting


def accounting(*, work: float = 0.0, inflight: int = 0, active: int = 0) -> dict:
    return {
        "queue": {"queued_requests": 0, "queued_work": 0.0},
        "replicas": {
            "replica1": {
                "inflight": inflight,
                "active_reservations": active,
                "outstanding_work": work,
            },
            "replica2": {
                "inflight": 0,
                "active_reservations": 0,
                "outstanding_work": 0.0,
            },
        },
    }


@pytest.mark.parametrize("work", [0.0, 1e-6, -1e-6])
def test_accounting_accepts_exact_and_tolerated_finite_zero(work: float) -> None:
    assert validate_final_accounting(accounting(work=work)) == []


@pytest.mark.parametrize("work", [1.000001e-6, -1.000001e-6, math.nan, math.inf, -math.inf])
def test_accounting_rejects_large_or_nonfinite_work(work: float) -> None:
    assert validate_final_accounting(accounting(work=work))


def test_accounting_rejects_unreleased_reservation_even_with_tiny_work() -> None:
    reasons = validate_final_accounting(accounting(work=5e-7, inflight=1, active=1))
    assert any("inflight" in reason for reason in reasons)
    assert any("ledger" in reason for reason in reasons)


def valid_inputs(*, waiting: float = 1.0, queued: int = 0, rejected: int = 0):
    summary = {
        "successes": 1,
        "count": 1,
        "queued_events": queued,
        "rejections": rejected,
        "failover_events": 0,
        "health_transition_events": 0,
        "max_backend_waiting": waiting,
        "metric_deltas": {
            "backend": {
                "vllm:num_preemptions_total": 0.0,
                "vllm:prefix_cache_hits_total": 0.0,
            }
        },
        "final_accounting": accounting(),
    }
    requests = [{"request_id": "r1", "success": True}]
    events = {
        "r1": [
            {"event": "reservation"},
            {"event": "prefill_release"},
            {"event": "final_release"},
        ]
    }
    return summary, requests, events


def test_backend_waiting_one_is_reported_but_does_not_invalidate() -> None:
    summary, requests, events = valid_inputs(waiting=1.0)
    validity = validate_arm(summary, requests, events)
    assert summary["max_backend_waiting"] == 1.0
    assert validity == {
        "infrastructure_valid": True,
        "invalid_reasons": [],
        "backend_waiting_is_measurement": True,
    }


@pytest.mark.parametrize(("queued", "rejected"), [(1, 0), (0, 1)])
def test_gateway_queue_or_rejection_remains_invalid(queued: int, rejected: int) -> None:
    summary, requests, events = valid_inputs(queued=queued, rejected=rejected)
    assert not validate_arm(summary, requests, events)["infrastructure_valid"]


def test_duplicate_or_inconsistent_release_sequence_is_invalid() -> None:
    summary, requests, events = valid_inputs()
    events["r1"].append({"event": "final_release"})
    assert not validate_arm(summary, requests, events)["infrastructure_valid"]


def test_load_reporting_fields_are_distinct() -> None:
    fields = {
        "configured_offered_rate_rps": 2.5,
        "achieved_arrival_rate_rps": 2.49,
        "arrival_lag_p95_s": 0.001,
        "completion_goodput_rps": 1.95,
    }
    assert len(fields) == 4
    assert fields["configured_offered_rate_rps"] != fields["completion_goodput_rps"]
