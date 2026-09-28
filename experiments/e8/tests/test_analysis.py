from __future__ import annotations

from analyze import EXPECTED_FROZEN, pair_reasons


def manifest(configuration: str) -> dict:
    return {
        **EXPECTED_FROZEN,
        "configuration": configuration,
        "rate": 8.0,
        "seed": 82001,
        "trace_sha256": "same",
        "summary": {
            "offered_count": 256,
            "recorded_count": 256,
            "outcome_reconciliation_ok": True,
        },
    }


def test_pair_identity_accepts_matching_frozen_arms() -> None:
    assert pair_reasons({"A": manifest("A"), "B": manifest("B")}, rate=8, seed=82001) == []


def test_pair_identity_rejects_trace_count_and_frozen_drift() -> None:
    left, right = manifest("A"), manifest("B")
    right["trace_sha256"] = "different"
    right["count"] = 255
    right["summary"]["recorded_count"] = 255
    reasons = pair_reasons({"A": left, "B": right}, rate=8, seed=82001)
    assert any("trace hash mismatch" in reason for reason in reasons)
    assert any("frozen field count mismatch" in reason for reason in reasons)
    assert any("arm count mismatch" in reason for reason in reasons)
