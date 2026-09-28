#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from e7.metrics import confusion, percentile


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [row for row in rows if row["success"]]
    return {
        "offered": len(rows),
        "successes": len(successes),
        "unexpected_failures": sum(not row["success"] for row in rows),
        "slo_misses": sum(row["slo_miss"] is True for row in rows),
        "ttft_misses": sum(row["ttft_miss"] is True for row in rows),
        "tpot_misses": sum(row["tpot_miss"] is True for row in rows),
        "e2e_misses": sum(row["e2e_miss"] is True for row in rows),
        "latency_s": {
            metric: {
                "p50": percentile([row[metric] for row in successes], 0.5),
                "p95": percentile([row[metric] for row in successes], 0.95),
            }
            for metric in ("ttft_s", "tpot_s", "e2e_s")
        },
        "predictors": {
            name: confusion(rows, name)
            for name in ("always_pass", "count_threshold", "work_threshold")
        },
        "prediction_computation_ns": {
            "p50": percentile([row["prediction_computation_ns"] for row in rows], 0.5),
            "p95": percentile([row["prediction_computation_ns"] for row in rows], 0.95),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    arm_dirs = sorted(args.root.glob("e7-measured-*/"))
    if len(arm_dirs) != 9:
        parser.error(f"expected nine measured arms, found {len(arm_dirs)}")
    rows_by_rate: dict[float, list[dict[str, Any]]] = defaultdict(list)
    per_run = []
    all_rows = []
    for arm in arm_dirs:
        summary = json.loads((arm / "summary.json").read_text())
        if (
            not summary["infrastructure_valid"]
            or summary.get("prediction_capture_complete") is False
            or summary.get("prediction_evaluation_complete") is False
        ):
            parser.error(f"invalid arm: {arm.name}")
        rows = json.loads((arm / "requests.json").read_text())
        rate = float(summary["configured_offered_rate_rps"])
        rows_by_rate[rate].extend(rows)
        all_rows.extend(rows)
        per_run.append(summary)
    payload = {
        "aggregation": "request rows pooled within rate and overall; percentiles recomputed",
        "per_run": per_run,
        "by_rate": {str(rate): summarize(rows) for rate, rows in sorted(rows_by_rate.items())},
        "overall": summarize(all_rows),
    }
    if args.output.exists():
        parser.error("output already exists")
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload["overall"], sort_keys=True))


if __name__ == "__main__":
    main()
