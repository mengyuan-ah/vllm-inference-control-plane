from __future__ import annotations

import pytest

from e6.trace import generate_trace, trace_sha256


def test_trace_is_exact_homogeneous_and_reproducible() -> None:
    first = generate_trace(count=64, rate=6, seed=61001, stage="pilot")
    second = generate_trace(count=64, rate=6, seed=61001, stage="pilot")
    assert first == second
    assert trace_sha256(first) == trace_sha256(second)
    assert all(row["input_tokens"] == 1024 for row in first)
    assert all(row["requested_output_tokens"] == 128 for row in first)
    assert first[-1]["scheduled_offset_s"] < 64 / 6


def test_pilot_and_measured_seeds_are_separate() -> None:
    with pytest.raises(ValueError):
        generate_trace(count=256, rate=4, seed=61001, stage="measured")
