from __future__ import annotations

import pytest

from e9.trace import content_sha256, generate_trace, scheduled_trace_sha256


def test_exact_equal_average_schedules_and_hashes() -> None:
    smooth = generate_trace(count=256, seed=92001, stage="measured", traffic="smooth")
    bursty = generate_trace(count=256, seed=92001, stage="measured", traffic="bursty")
    assert [row["scheduled_offset_s"] for row in smooth] == [index / 8 for index in range(256)]
    assert [row["scheduled_offset_s"] for row in bursty] == [
        4 * cycle + within / 16 for cycle in range(8) for within in range(32)
    ]
    assert len(smooth) == len(bursty) == 256
    assert content_sha256(smooth) == content_sha256(bursty)
    assert scheduled_trace_sha256(smooth) != scheduled_trace_sha256(bursty)
    assert smooth[-1]["scheduled_offset_s"] == 31.875
    assert bursty[-1]["scheduled_offset_s"] == 29.9375


def test_a_b_generation_is_identical_and_seed_domains_are_separate() -> None:
    left = generate_trace(count=256, seed=92002, stage="measured", traffic="bursty")
    right = generate_trace(count=256, seed=92002, stage="measured", traffic="bursty")
    assert left == right
    assert len({row["request_id"] for row in left}) == 256
    assert all(row["input_tokens"] == 1024 for row in left)
    assert all(row["requested_output_tokens"] == 128 for row in left)
    with pytest.raises(ValueError):
        generate_trace(count=256, seed=92001, stage="smoke", traffic="smooth")
    with pytest.raises(ValueError):
        generate_trace(count=257, seed=92001, stage="measured", traffic="smooth")
