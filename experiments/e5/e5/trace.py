from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from typing import Any

FROZEN_BETA = 107.2820167
PILOT_SEEDS = frozenset({51001, 51002, 51003, 51004})
MEASURED_SEEDS = frozenset({52001, 52002, 52003})

SHAPES = {
    "homogeneous": (("standard", 1024, 128, 1),),
    "mixed": (
        ("standard", 1024, 128, 2),
        ("prefill_heavy", 4096, 64, 1),
        ("decode_heavy", 256, 512, 1),
    ),
}


def estimated_work(input_tokens: int, requested_output_tokens: int) -> float:
    return input_tokens + FROZEN_BETA * requested_output_tokens


def _allowed_seed(stage: str, seed: int) -> None:
    allowed = PILOT_SEEDS if stage == "pilot" else MEASURED_SEEDS if stage == "measured" else None
    if allowed is None:
        raise ValueError("stage must be pilot or measured")
    if seed not in allowed:
        raise ValueError(f"seed {seed} is not reserved for {stage}")


def generate_trace(
    workload: str, *, request_count: int, rate: float, seed: int, stage: str
) -> list[dict[str, Any]]:
    """Generate one immutable trace to be consumed by both policy arms."""
    _allowed_seed(stage, seed)
    if workload not in SHAPES or request_count <= 0 or rate <= 0:
        raise ValueError("invalid workload, request_count, or rate")
    weighted = [item for item in SHAPES[workload] for _ in range(item[3])]
    if request_count % len(weighted):
        raise ValueError("request_count must preserve the fixed workload composition")
    classes = weighted * (request_count // len(weighted))
    rng = random.Random(seed)
    rng.shuffle(classes)
    elapsed = 0.0
    trace = []
    for index, (name, input_tokens, output_tokens, _) in enumerate(classes):
        if index:
            elapsed += -math.log1p(-rng.random()) / rate
        trace.append(
            {
                "request_id": f"e5-{stage}-{workload}-{seed}-{index:04d}",
                "scheduled_offset_s": elapsed,
                "class": name,
                "input_tokens": input_tokens,
                "requested_output_tokens": output_tokens,
                "prompt_seed": seed * 100_000 + index,
            }
        )
    expected = Counter(name for name, _, _, weight in SHAPES[workload] for _ in range(weight))
    actual = Counter(item["class"] for item in trace)
    multiplier = request_count // len(weighted)
    assert actual == Counter({key: value * multiplier for key, value in expected.items()})
    return trace


def trace_sha256(trace: list[dict[str, Any]]) -> str:
    payload = json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def paired_arm_manifest(trace: list[dict[str, Any]]) -> dict[str, Any]:
    digest = trace_sha256(trace)
    return {
        "trace_sha256": digest,
        "arms": [
            {"routing_mode": "round_robin", "trace_sha256": digest},
            {"routing_mode": "work_aware", "trace_sha256": digest},
        ],
    }
