from __future__ import annotations

import pytest

from e7.trace import generate_trace, trace_sha256


def test_trace_is_exact_homogeneous_and_reproducible() -> None:
    first = generate_trace(count=96, rate=10, seed=71001, stage="calibration")
    assert first == generate_trace(count=96, rate=10, seed=71001, stage="calibration")
    assert trace_sha256(first) == trace_sha256(list(first))
    assert all(
        row["input_tokens"] == 1024 and row["requested_output_tokens"] == 128 for row in first
    )


def test_calibration_and_measured_seeds_are_separate() -> None:
    with pytest.raises(ValueError):
        generate_trace(count=256, rate=10, seed=71001, stage="measured")
