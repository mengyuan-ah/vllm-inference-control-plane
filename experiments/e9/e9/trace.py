from __future__ import annotations

import hashlib
import json
from typing import Any

OFFER_WINDOW_S = 32.0
MEASURED_COUNT = 256
SMOKE_SEEDS = frozenset({90001})
PILOT_SEEDS = frozenset({91001})
MEASURED_SEEDS = frozenset({92001, 92002, 92003})


def _offset(index: int, traffic: str) -> float:
    if traffic == "smooth":
        return index / 8
    if traffic == "bursty":
        cycle, within = divmod(index, 32)
        return 4 * cycle + within / 16
    raise ValueError(f"unknown traffic pattern: {traffic}")


def generate_trace(*, count: int, seed: int, stage: str, traffic: str) -> list[dict[str, Any]]:
    pools = {"smoke": SMOKE_SEEDS, "pilot": PILOT_SEEDS, "measured": MEASURED_SEEDS}
    if stage not in pools or seed not in pools[stage] or count <= 0 or count > MEASURED_COUNT:
        raise ValueError("invalid stage, seed, or count")
    trace = []
    for index in range(count):
        offset = _offset(index, traffic)
        cycle = min(int(offset // 4), 7)
        trace.append(
            {
                "request_id": f"e9-{stage}-{seed}-{index:04d}",
                "scheduled_offset_s": offset,
                "input_tokens": 1024,
                "requested_output_tokens": 128,
                "prompt_seed": seed * 100_000 + index,
                "cycle_index": cycle,
                "cycle_offer_index": index - cycle * 32,
                "cycle_phase": "high" if offset < 4 * cycle + 2 else "quiet",
                "traffic": traffic,
            }
        )
    return trace


def _hash(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def content_sha256(trace: list[dict[str, Any]]) -> str:
    fields = ("request_id", "input_tokens", "requested_output_tokens", "prompt_seed")
    return _hash([{key: row[key] for key in fields} for row in trace])


def scheduled_trace_sha256(trace: list[dict[str, Any]]) -> str:
    return _hash(trace)
