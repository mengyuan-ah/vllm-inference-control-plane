from __future__ import annotations

from collections import Counter

import pytest

from e5.trace import FROZEN_BETA, estimated_work, generate_trace, paired_arm_manifest


def test_frozen_beta_calculation() -> None:
    assert estimated_work(1024, 128) == 1024 + 107.2820167 * 128
    assert FROZEN_BETA == 107.2820167


def test_mixed_trace_is_deterministic_and_fixed_composition() -> None:
    first = generate_trace("mixed", request_count=256, rate=3.0, seed=51001, stage="pilot")
    second = generate_trace("mixed", request_count=256, rate=3.0, seed=51001, stage="pilot")
    assert first == second
    assert Counter(item["class"] for item in first) == {
        "standard": 128,
        "prefill_heavy": 64,
        "decode_heavy": 64,
    }
    assert len({item["request_id"] for item in first}) == 256


def test_paired_arms_reference_identical_frozen_trace() -> None:
    trace = generate_trace("homogeneous", request_count=128, rate=2.5, seed=52001, stage="measured")
    manifest = paired_arm_manifest(trace)
    assert {arm["trace_sha256"] for arm in manifest["arms"]} == {manifest["trace_sha256"]}
    assert [arm["routing_mode"] for arm in manifest["arms"]] == [
        "round_robin",
        "work_aware",
    ]


@pytest.mark.parametrize(
    ("stage", "seed"), [("pilot", 52001), ("measured", 51001), ("unknown", 51001)]
)
def test_pilot_and_measured_seeds_cannot_mix(stage: str, seed: int) -> None:
    with pytest.raises(ValueError):
        generate_trace("mixed", request_count=256, rate=3.0, seed=seed, stage=stage)
