from __future__ import annotations

import hashlib
import json

SETTINGS = dict(
    backend_window_s=10.0,
    minimum_samples=4,
    degrade_ttft_s=0.350,
    recover_ttft_s=0.200,
    beta=107.2820167,
    rate=3.0,
    offer_window_s=90.0,
    offered=270,
    slowdown_start_s=15.0,
    slowdown_end_s=45.0,
    delay_s=0.750,
    input_tokens=1024,
    output_tokens=128,
    routing_mode="round_robin",
    max_inflight=8,
    max_work=100000.0,
    degraded_max_inflight=1,
    degraded_max_work=20000.0,
    queue=0,
    prediction_admission="disabled",
    retry_limit=1,
    model="Qwen/Qwen3-4B-Instruct-2507",
    revision="cdbee75f17c01a7cc42f958dc650907174af0554",
)
CATALOG = [
    dict(
        arm_id=f"r{rep}-{arm}",
        pair_id=f"r{rep}",
        arm=arm,
        seed=111000 + rep,
        target=f"replica{2 if rep == 2 else 1}",
        order=index,
    )
    for index, (rep, arm) in enumerate([(1, "A"), (1, "B"), (2, "B"), (2, "A"), (3, "A"), (3, "B")])
]


def trace_hash(rows):
    fields = (
        "request_id",
        "scheduled_offset_s",
        "prompt_sha256",
        "token_ids_sha256",
        "input_tokens",
        "max_tokens",
        "seed",
    )
    canonical = [[row[field] for field in fields] for row in rows]
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()
