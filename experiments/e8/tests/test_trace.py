from __future__ import annotations

import pytest

from e8.trace import generate_trace, trace_sha256


def test_paired_configs_receive_identical_trace() -> None:
    left = generate_trace(count=256, rate=10, seed=82001, stage="measured")
    right = generate_trace(count=256, rate=10, seed=82001, stage="measured")
    assert left == right
    assert trace_sha256(left) == trace_sha256(right)
    assert len({row["request_id"] for row in left}) == 256
    assert all(row["input_tokens"] == 1024 for row in left)
    assert all(row["requested_output_tokens"] == 128 for row in left)
    assert [row["scheduled_offset_s"] for row in left] == sorted(
        row["scheduled_offset_s"] for row in left
    )


def test_seed_domains_are_frozen_by_stage() -> None:
    with pytest.raises(ValueError):
        generate_trace(count=16, rate=11, seed=82001, stage="smoke")
    with pytest.raises(ValueError):
        generate_trace(count=0, rate=11, seed=80001, stage="smoke")
