#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

SEEDS = (92001, 92002, 92003)
CONDITIONS = ("smooth-A", "smooth-B", "bursty-A", "bursty-B")
EXPECTED_FROZEN = {
    "stage": "measured",
    "count": 256,
    "offer_window_s": 32.0,
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
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def descriptive(values: list[float]) -> dict[str, Any]:
    mean = statistics.mean(values)
    sample_sd = statistics.stdev(values)
    half_width = 4.3026527299 * sample_sd / math.sqrt(3)
    return {
        "values": values,
        "mean": mean,
        "range": [min(values), max(values)],
        "sample_standard_deviation": sample_sd,
        "descriptive_95pct_t_interval": [mean - half_width, mean + half_width],
    }


def analyze(root: Path, catalog_path: Path) -> dict[str, Any]:
    catalog = json.loads(catalog_path.read_text())
    catalog_hash = sha256(catalog_path)
    expected_order = [
        (entry["seed"], condition)
        for entry in catalog["repetitions"]
        for condition in entry["order"]
    ]
    manifests = [json.loads(path.read_text()) for path in root.glob("e9-measured-*/manifest.json")]
    invalid: list[str] = []
    indexed: dict[tuple[int, str], dict[str, Any]] = {}
    for manifest in manifests:
        condition = f"{manifest.get('traffic')}-{manifest.get('configuration')}"
        key = (manifest.get("seed"), condition)
        if key in indexed:
            invalid.append(f"duplicate arm {key}")
        indexed[key] = manifest
        if not manifest.get("evaluation_valid"):
            invalid.append(f"invalid arm {manifest.get('arm_id')}")
        for field, expected in EXPECTED_FROZEN.items():
            if manifest.get(field) != expected:
                invalid.append(
                    f"{manifest.get('arm_id')} frozen {field}: "
                    f"{manifest.get(field)!r} != {expected!r}"
                )
        if manifest.get("catalog_sha256") != catalog_hash:
            invalid.append(f"{manifest.get('arm_id')} catalog hash mismatch")
        summary = manifest.get("summary", {})
        if summary.get("offered_count") != 256 or summary.get("recorded_count") != 256:
            invalid.append(f"{manifest.get('arm_id')} count mismatch")
        if summary.get("measurement_window_s") != 32.0:
            invalid.append(f"{manifest.get('arm_id')} offer window mismatch")
        if summary.get("outcome_reconciliation_ok") is not True:
            invalid.append(f"{manifest.get('arm_id')} outcome reconciliation failed")
    expected_keys = set(expected_order)
    if set(indexed) != expected_keys:
        invalid.append(
            f"arm set mismatch missing={sorted(expected_keys - set(indexed))} "
            f"unexpected={sorted(set(indexed) - expected_keys)}"
        )

    for seed in SEEDS:
        arms = {condition: indexed.get((seed, condition)) for condition in CONDITIONS}
        if any(arm is None for arm in arms.values()):
            continue
        if len({arm["content_sha256"] for arm in arms.values()}) != 1:
            invalid.append(f"content identity mismatch seed={seed}")
        for traffic in ("smooth", "bursty"):
            if (
                arms[f"{traffic}-A"]["scheduled_trace_sha256"]
                != arms[f"{traffic}-B"]["scheduled_trace_sha256"]
            ):
                invalid.append(f"A/B scheduled trace mismatch traffic={traffic} seed={seed}")
        if arms["smooth-A"]["scheduled_trace_sha256"] == arms["bursty-A"]["scheduled_trace_sha256"]:
            invalid.append(f"smooth and bursty scheduled traces unexpectedly match seed={seed}")

    ordered = sorted(manifests, key=lambda item: item.get("arm_order_index", -1))
    actual_order = [
        (item.get("seed"), f"{item.get('traffic')}-{item.get('configuration')}") for item in ordered
    ]
    if actual_order != expected_order:
        invalid.append(f"actual arm order mismatch: {actual_order}")
    for previous, current in zip(ordered, ordered[1:], strict=False):
        previous_end = dt.datetime.fromisoformat(previous["arm_end_utc"])
        current_start = dt.datetime.fromisoformat(current["arm_start_utc"])
        if current_start < previous_end:
            invalid.append(
                f"arm UTC intervals overlap: {previous['arm_id']} -> {current['arm_id']}"
            )

    contrasts: dict[str, list[float]] = {
        "admission_effect_smooth_b_minus_a": [],
        "admission_effect_bursty_b_minus_a": [],
        "burstiness_effect_a_bursty_minus_smooth": [],
        "burstiness_effect_b_bursty_minus_smooth": [],
        "interaction_bursty_admission_minus_smooth_admission": [],
    }
    for seed in SEEDS:
        if any((seed, condition) not in indexed for condition in CONDITIONS):
            continue
        values = {
            condition: indexed[(seed, condition)]["summary"]["slo_goodput_rps"]
            for condition in CONDITIONS
        }
        smooth = values["smooth-B"] - values["smooth-A"]
        bursty = values["bursty-B"] - values["bursty-A"]
        contrasts["admission_effect_smooth_b_minus_a"].append(smooth)
        contrasts["admission_effect_bursty_b_minus_a"].append(bursty)
        contrasts["burstiness_effect_a_bursty_minus_smooth"].append(
            values["bursty-A"] - values["smooth-A"]
        )
        contrasts["burstiness_effect_b_bursty_minus_smooth"].append(
            values["bursty-B"] - values["smooth-B"]
        )
        contrasts["interaction_bursty_admission_minus_smooth_admission"].append(bursty - smooth)

    condition_backlog = {}
    for condition in CONDITIONS:
        arms = [indexed[(seed, condition)] for seed in SEEDS if (seed, condition) in indexed]
        if len(arms) == 3:
            condition_backlog[condition] = {
                peak: [arm["backlog"]["peaks"][peak] for arm in arms]
                for peak in (
                    "gateway_queue_depth",
                    "gateway_outstanding_requests",
                    "gateway_outstanding_work",
                    "backend_running",
                    "backend_waiting",
                )
            }
            condition_backlog[condition]["cycles_drained_before_next_burst"] = [
                sum(cycle["reached_zero_before_next_burst"] for cycle in arm["backlog"]["cycles"])
                for arm in arms
            ]

    result = {
        "valid": not invalid and len(manifests) == 12,
        "invalid_reasons": invalid,
        "manifest_count": len(manifests),
        "catalog_sha256": catalog_hash,
        "expected_arm_order": expected_order,
        "actual_arm_order": actual_order,
        "contrasts": {
            name: descriptive(values) for name, values in contrasts.items() if len(values) == 3
        },
        "condition_backlog": condition_backlog,
        "interpretation_limits": [
            (
                "The schedules are deterministic and do not provide independent "
                "arrival-pattern samples."
            ),
            (
                "Repetitions vary prompts and execution conditions; inference applies "
                "only to this burst shape."
            ),
            (
                "Quiet intervals contain no probes, so cohort outcomes at later arrivals "
                "are not exact continuous time-to-SLO-recovery measurements."
            ),
        ],
    }
    (root / "e9-analysis.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output_root", type=Path)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path(__file__).with_name("ARM_CATALOG.json"),
    )
    args = parser.parse_args()
    outcome = analyze(args.output_root, args.catalog)
    print(json.dumps(outcome, indent=2))
    raise SystemExit(0 if outcome["valid"] else 2)
