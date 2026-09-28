from __future__ import annotations

import hashlib
import json

from analyze import analyze


def test_strict_twelve_arm_analysis(tmp_path) -> None:
    catalog = {
        "protocol": "e9-equal-average-v1",
        "measured_count_per_arm": 256,
        "offer_window_s": 32,
        "seeds": [92001, 92002, 92003],
        "repetitions": [
            {
                "seed": 92001,
                "order": ["smooth-A", "bursty-B", "smooth-B", "bursty-A"],
            },
            {
                "seed": 92002,
                "order": ["bursty-A", "smooth-B", "bursty-B", "smooth-A"],
            },
            {
                "seed": 92003,
                "order": ["bursty-B", "bursty-A", "smooth-A", "smooth-B"],
            },
        ],
    }
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    catalog_hash = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    start_second = 0
    order_index = 1
    for repetition in catalog["repetitions"]:
        seed = repetition["seed"]
        for condition in repetition["order"]:
            traffic, configuration = condition.split("-")
            arm = tmp_path / f"e9-measured-{traffic}-{configuration}-s{seed}"
            arm.mkdir()
            manifest = {
                "arm_id": arm.name,
                "stage": "measured",
                "traffic": traffic,
                "configuration": configuration,
                "count": 256,
                "seed": seed,
                "arm_order_index": order_index,
                "catalog_sha256": catalog_hash,
                "content_sha256": f"content-{seed}",
                "scheduled_trace_sha256": f"schedule-{seed}-{traffic}",
                "offer_window_s": 32.0,
                "arm_start_utc": f"2026-01-01T00:{start_second:02d}:00+00:00",
                "arm_end_utc": f"2026-01-01T00:{start_second:02d}:30+00:00",
                "model": "Qwen/Qwen3-4B-Instruct-2507",
                "model_revision": "cdbee75f17c01a7cc42f958dc650907174af0554",
                "input_tokens": 1024,
                "output_tokens": 128,
                "beta": 107.2820167,
                "count_threshold": 8,
                "routing": "round_robin",
                "queue_enabled": False,
                "prefix_cache_enabled": False,
                "backend_retry_limit": 0,
                "replica_count": 2,
                "evaluation_valid": True,
                "summary": {
                    "offered_count": 256,
                    "recorded_count": 256,
                    "measurement_window_s": 32.0,
                    "outcome_reconciliation_ok": True,
                    "slo_goodput_rps": 7.0 + (configuration == "B"),
                },
                "backlog": {
                    "peaks": {
                        "gateway_queue_depth": 0,
                        "gateway_outstanding_requests": 8,
                        "gateway_outstanding_work": 10.0,
                        "backend_running": 8,
                        "backend_waiting": 0,
                    },
                    "cycles": [{"reached_zero_before_next_burst": True} for _ in range(8)],
                },
            }
            (arm / "manifest.json").write_text(json.dumps(manifest))
            order_index += 1
            start_second += 1
    result = analyze(tmp_path, catalog_path)
    assert result["valid"] is True
    assert result["manifest_count"] == 12
    assert result["contrasts"]["admission_effect_smooth_b_minus_a"]["values"] == [
        1.0,
        1.0,
        1.0,
    ]
