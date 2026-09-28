from __future__ import annotations

import hashlib
import json
import random
from typing import Any

SMOKE_SEEDS = frozenset({70001})
CALIBRATION_SEEDS = frozenset({71001, 71002, 71003, 71004, 71005})
MEASURED_SEEDS = frozenset({72001, 72002, 72003})


def generate_trace(*, count: int, rate: float, seed: int, stage: str) -> list[dict[str, Any]]:
    pools = {"smoke": SMOKE_SEEDS, "calibration": CALIBRATION_SEEDS, "measured": MEASURED_SEEDS}
    if stage not in pools or seed not in pools[stage] or count <= 0 or rate <= 0:
        raise ValueError("invalid stage, reserved seed, count, or rate")
    rng = random.Random(seed)
    jitter = [rng.uniform(-0.2, 0.2) / rate for _ in range(count)]
    offsets = sorted(max(0.0, index / rate + jitter[index]) for index in range(count))
    return [
        {
            "request_id": f"e7-{stage}-{seed}-{index:04d}",
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
