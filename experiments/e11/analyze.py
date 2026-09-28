from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "e10"))
from e10.runtime import classify_outcome  # noqa: E402

from e11.protocol import CATALOG, SETTINGS, trace_hash  # noqa: E402


def read(path):
    return json.loads(path.read_text())


def arm_result(path, expected=None):
    failures = []
    m = read(path / "manifest.json")
    rows = read(path / "requests.json")
    events = read(path / "lifecycle.json")
    injections = read(path / "injection.json")
    samples = read(path / "backend_samples.json")
    observations = read(path / "detector.json")
    settings = m["settings"]
    config = m.get("gateway_config") or {}
    if (
        any(
            config.get(k) != v
            for k, v in {
                "routing_mode": "round_robin",
                "beta": 107.2820167,
                "backend_retry_limit": 1,
                "max_queued_requests": 0,
                "prediction_admission_mode": "disabled",
                "lifecycle_enabled": True,
                "slowdown_enabled": m["arm"] == "B",
            }.items()
        )
        or len(config.get("replicas", [])) != 2
    ):
        failures.append("gateway_configuration")
    for replica in config.get("replicas", []):
        if any(
            replica.get(k) != v
            for k, v in {
                "max_inflight": 8,
                "max_work": 100000.0,
                "degraded_max_inflight": 1,
                "degraded_max_work": 20000.0,
                "routing_capacity": 1.0,
            }.items()
        ):
            failures.append("replica_configuration")
    if expected is not None and (
        any(m.get(k) != v for k, v in expected.items())
        or settings != SETTINGS
        or m["stage"] != "measured"
    ):
        failures.append("catalog_or_frozen_settings_mismatch")
    if m.get("execution_error"):
        failures.append("execution_error")
    if len(rows) != settings["offered"] or len({r["request_id"] for r in rows}) != len(rows):
        failures.append("offer_identity_or_count")
    if trace_hash(rows) != m["trace_sha256"]:
        failures.append("trace_hash_mismatch")
    for i, row in enumerate(rows):
        if (
            row["seed"] != m["seed"]
            or row["input_tokens"] != 1024
            or row["max_tokens"] != 128
            or row["scheduled_offset_s"] != i / settings["rate"]
        ):
            failures.append("trace_shape_or_schedule")
        if (
            hashlib.sha256(row["prompt"].encode()).hexdigest() != row["prompt_sha256"]
            or hashlib.sha256(json.dumps(row["input_token_ids"]).encode()).hexdigest()
            != row["token_ids_sha256"]
            or len(row["input_token_ids"]) != 1024
        ):
            failures.append("request_content_hash")
        expected_slo = bool(
            row.get("success")
            and all(
                isinstance(row.get(k), (int, float)) and 0 <= row[k] <= limit
                for k, limit in (("ttft_s", 0.250), ("tpot_s", 0.015), ("e2e_s", 2.0))
            )
        )
        if row.get("slo_success") != expected_slo:
            failures.append("slo_accounting")
        label = classify_outcome(dict(row))
        if label == "invalid_evidence" or label != row["outcome"]:
            failures.append("client_outcome_evidence")
        if row.get("retry_count", 0) > 1:
            failures.append("retry_limit")
        if not math.isfinite(row.get("arrival_lag_s", float("nan"))) or not (
            0 <= row["arrival_lag_s"] <= 0.100
        ):
            failures.append("arrival_lag")
        routing_events = row.get("routing_events", [])
        if sum(e["event"] == "reservation" for e in routing_events) != sum(
            e["event"] == "final_release" for e in routing_events
        ):
            failures.append("reservation_release_count")
        first_content = [e["timestamp"] for e in routing_events if e["event"] == "prefill_release"]
        if first_content and any(
            e["event"] == "backend_dispatch" and e["timestamp"] > min(first_content)
            for e in routing_events
        ):
            failures.append("retry_after_content")
        dispatches = [e for e in row.get("routing_events", []) if e["event"] == "backend_dispatch"]
        if row.get("content_received") and len(dispatches) > 1 and row.get("retry_count") != 1:
            failures.append("attempt_accounting")
    if len(observations) != sum(r.get("gateway_state") == "Completed" for r in rows):
        failures.append("backend_observation_coverage")
    replicas = m.get("final_accounting", {}).get("replicas", {})
    if set(replicas) != {"replica1", "replica2"} or any(
        r.get("inflight") or r.get("outstanding_work") or r.get("active_reservations")
        for r in replicas.values()
    ):
        failures.append("final_accounting")
    if not samples or any(
        not s[name]["healthy"] or s[name]["waiting"] != 0 for s in samples for name in replicas
    ):
        failures.append("backend_health_or_waiting")
    for name in replicas:
        timestamps = [s[name]["timestamp"] for s in samples]
        if any(b - a > 1.0 or b < a for a, b in zip(timestamps, timestamps[1:], strict=False)):
            failures.append("backend_sampling_gap")
        for counter in ("preemptions", "cache_hits"):
            values = [s[name][counter] for s in samples]
            if not values or any(value != values[0] for value in values):
                failures.append("backend_counter_changed")
    if any(e["to"] in {"Unhealthy", "HalfOpen", "Recovering"} for e in events):
        failures.append("unexpected_failure_lifecycle")
    markers = [e for e in injections if e["event"] in {"injection_start", "injection_end"}]
    if m["stage"] != "calibration" and (
        len(markers) != 2 or {e["event"] for e in markers} != {"injection_start", "injection_end"}
    ):
        failures.append("injection_markers")
    for e in markers:
        offset = (
            settings["slowdown_start_s"]
            if e["event"] == "injection_start"
            else settings["slowdown_end_s"]
        )
        if (
            e["replica_id"] != m["target"]
            or e["delay_s"] != settings["delay_s"]
            or abs(e["scheduled"] - m["start_monotonic"] - offset) > 1e-6
            or abs(e["timestamp"] - e["scheduled"]) > 0.100
        ):
            failures.append("injection_identity_or_timing")
    for e in injections:
        if e["event"] == "shim_dispatch":
            offset = e["received"] - m["start_monotonic"]
            desired = (
                settings["delay_s"]
                if e["replica_id"] == m["target"]
                and (settings["slowdown_start_s"] <= offset < settings["slowdown_end_s"])
                else 0
            )
            if e["scheduled_delay_s"] != desired or abs(e["actual_delay_s"] - desired) > 0.100:
                failures.append("applied_delay")
    if m["arm"] == "A" and any(e["to"] == "Degraded" for e in events):
        failures.append("disabled_arm_transition")
    dispatches = [
        e for row in rows for e in row.get("routing_events", []) if e["event"] == "backend_dispatch"
    ]
    expected_dispatches = Counter(
        (row["request_id"], e["selected_replica"])
        for row in rows
        for e in row.get("routing_events", [])
        if e["event"] == "backend_dispatch"
    )
    actual_dispatches = Counter(
        (e["request_id"], e["replica_id"])
        for e in injections
        if e["event"] == "shim_dispatch" and e.get("request_id")
    )
    if expected_dispatches != actual_dispatches:
        failures.append("shim_dispatch_coverage")
    phase_dispatch = {}
    for phase, low, high in [
        ("before", 0, settings["slowdown_start_s"]),
        ("slow", settings["slowdown_start_s"], settings["slowdown_end_s"]),
        ("after", settings["slowdown_end_s"], settings["offer_window_s"]),
    ]:
        selected = [e for e in dispatches if low <= e["timestamp"] - m["start_monotonic"] < high]
        target_count = sum(e["selected_replica"] == m["target"] for e in selected)
        phase_dispatch[phase] = dict(
            total=len(selected),
            target=target_count,
            target_fraction=target_count / len(selected) if selected else None,
        )
    degraded = [
        e
        for e in events
        if e["to"] == "Degraded"
        and e["replica_id"] == m["target"]
        and m["start_monotonic"]
        <= e["timestamp"]
        <= m["start_monotonic"] + settings["offer_window_s"]
    ]
    restored = [
        e
        for e in events
        if e["to"] == "Healthy"
        and e["replica_id"] == m["target"]
        and m["start_monotonic"] + settings["slowdown_end_s"]
        <= e["timestamp"]
        <= m["start_monotonic"] + settings["offer_window_s"]
    ]
    false = [
        e
        for e in events
        if e["to"] == "Degraded"
        and (
            e["replica_id"] != m["target"]
            or e["timestamp"] < m["start_monotonic"] + settings["slowdown_start_s"]
        )
    ]
    return dict(
        valid=not failures,
        failures=sorted(set(failures)),
        arm_id=m["arm_id"],
        source_commit=m["source_commit"],
        trace_sha256=m["trace_sha256"],
        start=m["start_monotonic"],
        end=m["end_monotonic"],
        phase_dispatch=phase_dispatch,
        slo_successes=sum(bool(r.get("slo_success")) for r in rows),
        slo_goodput=sum(bool(r.get("slo_success")) for r in rows) / settings["offer_window_s"],
        detection_delay_s=degraded[0]["timestamp"]
        - m["start_monotonic"]
        - settings["slowdown_start_s"]
        if degraded
        else None,
        recovery_delay_s=restored[0]["timestamp"]
        - m["start_monotonic"]
        - settings["slowdown_end_s"]
        if restored
        else None,
        recovery_censored=not restored if m["arm"] == "B" else None,
        false_degradations=len(false),
        observations=len(observations),
        outcomes=dict(Counter(r["outcome"] for r in rows)),
        latency_percentiles={
            field: {
                str(q): (sorted(v)[math.ceil(q * len(v)) - 1] if v else None)
                for q in (0.5, 0.95, 0.99)
            }
            for field in ("ttft_s", "tpot_s", "e2e_s")
            for v in [[r[field] for r in rows if r.get(field) is not None]]
        },
        transitions=len(events),
    )


def analyze(root):
    failures, arms, pairs = [], [], []
    actual = {p.parent.name for p in root.glob("*/manifest.json")}
    expected = {s["arm_id"] for s in CATALOG}
    if actual != expected or {p.name for p in root.iterdir() if p.is_dir()} != expected:
        failures.append("expected_exactly_six_catalog_arms")
    for spec in CATALOG:
        try:
            result = arm_result(root / spec["arm_id"], spec)
            arms.append(result)
            failures.extend(f"{spec['arm_id']}:{f}" for f in result["failures"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            failures.append(f"{spec['arm_id']}:incomplete_evidence:{type(exc).__name__}")
    if len(arms) == 6:
        if any(a["end"] > b["start"] for a, b in zip(arms, arms[1:], strict=False)):
            failures.append("catalog_order_or_overlap")
        if len({a["source_commit"] for a in arms}) != 1:
            failures.append("source_identity_mismatch")
        by_id = {a["arm_id"]: a for a in arms}
        for rep in (1, 2, 3):
            a, b = by_id[f"r{rep}-A"], by_id[f"r{rep}-B"]
            if a["trace_sha256"] != b["trace_sha256"]:
                failures.append(f"r{rep}:paired_trace_mismatch")
            pairs.append(
                dict(
                    pair_id=f"r{rep}",
                    goodput_B_minus_A=b["slo_goodput"] - a["slo_goodput"],
                    target_share_A=a["phase_dispatch"]["slow"]["target_fraction"],
                    target_share_B=b["phase_dispatch"]["slow"]["target_fraction"],
                )
            )
    return dict(
        valid=not failures, failures=failures, arms=arms, pairs=pairs if not failures else []
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.root)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    raise SystemExit(0 if result["valid"] else 2)


if __name__ == "__main__":
    main()
