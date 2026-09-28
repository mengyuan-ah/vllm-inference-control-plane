#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

EXPECTED_FROZEN = {
    "stage": "measured",
    "count": 256,
    "model": "Qwen/Qwen3-4B-Instruct-2507",
    "model_revision": "cdbee75f17c01a7cc42f958dc650907174af0554",
    "input_tokens": 1024,
    "output_tokens": 128,
    "beta": 107.2820167,
    "count_threshold": 8,
    "routing": "round_robin",
    "queue_enabled": False,
}


def pair_reasons(pair: dict[str, dict], *, rate: float, seed: int) -> list[str]:
    label = f"rate={rate:g} seed={seed}"
    if set(pair) != {"A", "B"}:
        return [f"missing or duplicate pair member {label}"]
    reasons: list[str] = []
    left, right = pair["A"], pair["B"]
    if left.get("trace_sha256") != right.get("trace_sha256"):
        reasons.append(f"trace hash mismatch {label}")
    for configuration, manifest in pair.items():
        for field, expected in EXPECTED_FROZEN.items():
            if manifest.get(field) != expected:
                reasons.append(
                    f"{configuration} frozen field {field} mismatch {label}: "
                    f"{manifest.get(field)!r} != {expected!r}"
                )
        if manifest.get("rate") != rate or manifest.get("seed") != seed:
            reasons.append(f"{configuration} pair identity mismatch {label}")
        summary = manifest.get("summary", {})
        if summary.get("offered_count") != 256 or summary.get("recorded_count") != 256:
            reasons.append(f"{configuration} arm count mismatch {label}")
        if summary.get("outcome_reconciliation_ok") is not True:
            reasons.append(f"{configuration} outcomes do not reconcile {label}")
    return reasons


def analyze(root: Path) -> dict:
    manifests = [json.loads(path.read_text()) for path in root.glob("e8-measured-*/manifest.json")]
    grouped: dict[tuple[float, int], dict[str, dict]] = {}
    invalid = []
    for manifest in manifests:
        if not manifest["evaluation_valid"]:
            invalid.append(manifest["arm_id"])
        pair = grouped.setdefault((manifest["rate"], manifest["seed"]), {})
        configuration = manifest["configuration"]
        if configuration in pair:
            invalid.append(
                f"duplicate configuration {configuration} rate={manifest['rate']:g} "
                f"seed={manifest['seed']}"
            )
        else:
            pair[configuration] = manifest
    rates: dict[str, dict] = {}
    for rate in (8.0, 10.0, 11.0):
        differences = []
        for seed in (82001, 82002, 82003):
            pair = grouped.get((rate, seed), {})
            reasons = pair_reasons(pair, rate=rate, seed=seed)
            invalid.extend(reasons)
            if reasons:
                continue
            differences.append(
                pair["B"]["summary"]["slo_goodput_rps"] - pair["A"]["summary"]["slo_goodput_rps"]
            )
        if len(differences) == 3:
            mean = statistics.mean(differences)
            standard_deviation = statistics.stdev(differences)
            half_width = 4.3026527299 * standard_deviation / math.sqrt(3)
            rates[f"{rate:g}"] = {
                "paired_differences_b_minus_a": differences,
                "mean": mean,
                "range": [min(differences), max(differences)],
                "sample_standard_deviation": standard_deviation,
                "descriptive_95pct_t_interval": [mean - half_width, mean + half_width],
            }
    result = {
        "valid": not invalid and len(manifests) == 18,
        "invalid_reasons": invalid,
        "manifest_count": len(manifests),
        "by_rate": rates,
    }
    (root / "paired-analysis.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output_root", type=Path)
    outcome = analyze(parser.parse_args().output_root)
    print(json.dumps(outcome, indent=2))
    raise SystemExit(0 if outcome["valid"] else 2)
