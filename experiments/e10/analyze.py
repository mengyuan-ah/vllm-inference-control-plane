from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from e10.metrics import paired_trace_identity, reconcile
from e10.runtime import classify_outcome

EXPECTED_SEEDS = {101001, 101002, 101003}
CONDITIONS = {"control", "fault"}


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def accounting_empty(value: dict[str, Any]) -> bool:
    if (
        not value
        or value.get("queue", {}).get("queued_requests")
        or value.get("queue", {}).get("queued_work")
    ):
        return False
    return all(
        not replica.get("inflight")
        and not replica.get("outstanding_work")
        and not replica.get("active_reservations")
        for replica in value.get("replicas", {}).values()
    )


def analyze(root: Path) -> dict[str, Any]:
    failures: list[str] = []
    manifests: list[dict[str, Any]] = []
    rows_by_arm: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(root.glob("*/manifest.json")):
        try:
            manifest = json.loads(path.read_text())
            manifests.append(manifest)
            request_path = path.parent / "requests.jsonl"
            rows_by_arm[manifest["arm_id"]] = read_rows(request_path)
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            failures.append(f"{path.parent.name}: unreadable evidence: {type(exc).__name__}")
    if len(manifests) != 6:
        failures.append(f"expected exactly 6 measured arms, found {len(manifests)}")
    arm_ids = [item.get("arm_id") for item in manifests]
    if len(set(arm_ids)) != len(arm_ids):
        failures.append("duplicate arm identity")
    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    seen_seeds: set[int] = set()
    phase_outcomes: Counter[tuple[str, str]] = Counter()
    recovery: list[dict[str, Any]] = []
    slo_success = 0
    offered = 0
    for manifest in manifests:
        arm_id = manifest.get("arm_id", "unknown")
        rows = rows_by_arm.get(arm_id, [])
        pair_id = manifest.get("pair_id")
        condition = manifest.get("condition")
        if condition not in CONDITIONS or not pair_id:
            failures.append(f"{arm_id}: invalid pair/condition")
            continue
        if condition in by_pair.setdefault(pair_id, {}):
            failures.append(f"{pair_id}: duplicate {condition} arm")
        by_pair[pair_id][condition] = manifest
        seeds = {row.get("seed") for row in rows}
        if len(seeds) == 1:
            seen_seeds.update(seeds)
        check = reconcile(rows, int(manifest.get("offered", -1)))
        if not check["valid"]:
            failures.append(f"{arm_id}: final request accounting is incomplete")
        if manifest.get("execution_error"):
            failures.append(f"{arm_id}: execution error")
        if not accounting_empty(manifest.get("final_accounting", {})):
            failures.append(f"{arm_id}: nonzero or missing final accounting")
        if condition == "fault":
            timeline_path = root / arm_id / "timeline.json"
            timeline = json.loads(timeline_path.read_text()) if timeline_path.exists() else []
            restart = next(
                (
                    event.get("actual")
                    for event in timeline
                    if event.get("event") == "restart_invocation"
                ),
                None,
            )
            lifecycle_events = []
            for row in rows:
                for event in row.get("routing_events", []):
                    if event.get("event") == "final_accounting":
                        lifecycle_events.extend(event.get("state", {}).get("lifecycle_events", []))
            healthy = [
                event.get("timestamp")
                for event in lifecycle_events
                if event.get("to") == "Healthy"
                and restart is not None
                and event.get("timestamp", 0) >= restart
            ]
            recovery.append(
                {
                    "arm_id": arm_id,
                    "restart_to_healthy_s": min(healthy) - restart if healthy else None,
                    "censored": not healthy,
                }
            )
        for row in rows:
            if manifest.get("outcome_schema_version") == 2 or "content_received" in row:
                expected = classify_outcome(dict(row))
                if expected == "invalid_evidence" or row.get("outcome") != expected:
                    failures.append(f"{arm_id}: inconsistent client outcome evidence")
            if row.get("slo_success") and not row.get("success"):
                failures.append(f"{arm_id}: failed request counted as SLO success")
            offered += 1
            slo_success += bool(row.get("slo_success"))
            phase_outcomes[(str(row.get("phase")), str(row.get("outcome")))] += 1
            if row.get("success") and any(
                row.get(field) is None for field in ("ttft_s", "tpot_s", "e2e_s")
            ):
                failures.append(f"{arm_id}: successful request lacks latency metrics")
            if row.get("input_tokens") != 1024 or row.get("max_tokens") != 128:
                failures.append(f"{arm_id}: request shape mismatch")
    if set(by_pair) and len(by_pair) != 3:
        failures.append(f"expected exactly 3 pairs, found {len(by_pair)}")
    if seen_seeds and seen_seeds != EXPECTED_SEEDS:
        failures.append(f"measured seeds mismatch: {sorted(seen_seeds)}")
    for pair_id, pair in by_pair.items():
        if set(pair) != CONDITIONS:
            failures.append(f"{pair_id}: incomplete pair")
            continue
        control = pair["control"]
        fault = pair["fault"]
        if any(
            control.get(field) != fault.get(field)
            for field in (
                "rate",
                "offer_window_s",
                "offered",
                "source_commit",
                "model",
                "model_revision",
            )
        ):
            failures.append(f"{pair_id}: frozen setting mismatch")
        if not paired_trace_identity(rows_by_arm[control["arm_id"]], rows_by_arm[fault["arm_id"]]):
            failures.append(f"{pair_id}: trace mismatch")
    return {
        "valid": not failures,
        "failures": sorted(set(failures)),
        "arms": len(manifests),
        "pairs": len(by_pair),
        "offered_rows": offered,
        "slo_success_count": slo_success,
        "slo_success_fraction": slo_success / offered if offered else None,
        "phase_outcomes": {
            f"{phase}:{outcome}": count
            for (phase, outcome), count in sorted(phase_outcomes.items())
        },
        "recovery": recovery,
        "counter_reset_policy": "delta is undefined across generations",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.root)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    raise SystemExit(0 if result["valid"] else 2)


if __name__ == "__main__":
    main()
