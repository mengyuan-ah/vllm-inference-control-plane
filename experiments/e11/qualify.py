from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyze import arm_result, read
from e11.protocol import SETTINGS


def check(root, source_commit):
    failures = []
    results = {}
    for name in ("calibration", "pilot-A", "pilot-B"):
        try:
            path = root / name
            m = read(path / "manifest.json")
            result = arm_result(path)
            results[name] = result
            expected_stage = "calibration" if name == "calibration" else "pilot"
            if m["stage"] != expected_stage or m["source_commit"] != source_commit:
                failures.append(name + ":stage_or_source")
            if not result["valid"]:
                failures.append(name + ":invalid")
            rows = read(path / "requests.json")
            if not all(r.get("success") for r in rows):
                failures.append(name + ":failed_or_rejected_offer")
            if name == "calibration":
                if (
                    len(m.get("backend_urls", [])) != 2
                    or m["backend_urls"][0] != m["backend_urls"][1]
                ):
                    failures.append("calibration:must_use_one_backend")
                expected = dict(
                    SETTINGS,
                    offered=60,
                    offer_window_s=20.0,
                    slowdown_start_s=100.0,
                    slowdown_end_s=110.0,
                )
                if m["settings"] != expected or m["seed"] != 110000 or len(rows) != 60:
                    failures.append("calibration:identity")
                if (
                    sum(bool(r.get("slo_success")) for r in rows) < 0.99 * 60
                    or sum(r.get("ttft_s", 999) < 0.2 for r in rows) < 0.99 * 60
                ):
                    failures.append("calibration:baseline_latency_gate")
            elif m["settings"] != SETTINGS or m["seed"] != 110001 or m["arm"] != name[-1]:
                failures.append(name + ":identity")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            failures.append(name + ":missing_or_invalid:" + type(exc).__name__)
    if "pilot-A" in results and "pilot-B" in results:
        a, b = results["pilot-A"], results["pilot-B"]
        if a["trace_sha256"] != b["trace_sha256"]:
            failures.append("pilot:trace_mismatch")
        if (
            b["detection_delay_s"] is None
            or b["recovery_delay_s"] is None
            or b["false_degradations"]
            or b["transitions"] != 2
        ):
            failures.append("pilot:detection_recovery_gate")
        aa, bb = (
            a["phase_dispatch"]["slow"]["target_fraction"],
            b["phase_dispatch"]["slow"]["target_fraction"],
        )
        if aa is None or bb is None or not bb < aa:
            failures.append("pilot:traffic_reduction_gate")
    return dict(valid=not failures, failures=failures, source_commit=source_commit, results=results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = check(args.root, args.source_commit)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    raise SystemExit(0 if result["valid"] else 2)


if __name__ == "__main__":
    main()
