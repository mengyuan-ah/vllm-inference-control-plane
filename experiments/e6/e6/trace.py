from __future__ import annotations

import hashlib
import json
import random
from typing import Any

PILOT_SEEDS = frozenset({61001, 61002, 61003})
MEASURED_SEEDS = frozenset({62001, 62002, 62003})


def generate_trace(*, count: int, rate: float, seed: int, stage: str) -> list[dict[str, Any]]:
    allowed = PILOT_SEEDS if stage == "pilot" else MEASURED_SEEDS if stage == "measured" else None
    if allowed is None or seed not in allowed or count <= 0 or rate <= 0:
        raise ValueError("invalid stage, reserved seed, count, or rate")
    rng = random.Random(seed)
    # Exactly count offered arrivals in the declared count/rate measurement window.
    jitter = [rng.uniform(-0.2, 0.2) / rate for _ in range(count)]
    offsets = [max(0.0, index / rate + jitter[index]) for index in range(count)]
    offsets.sort()
    return [
        {
            "request_id": f"e6-{stage}-{seed}-{index:04d}",
            "scheduled_offset_s": offset,
            "input_tokens": 1024,
            "requested_output_tokens": 128,
            "prompt_seed": seed * 100_000 + index,
        }
        for index, offset in enumerate(offsets)
    ]


def trace_sha256(trace: list[dict[str, Any]]) -> str:
    canonical = json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()
