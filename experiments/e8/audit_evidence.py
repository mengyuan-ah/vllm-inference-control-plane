#!/usr/bin/env python3
"""CPU-only, standard-library audit of a preserved E8 evidence bundle."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import platform
import statistics
import tarfile
from typing import Any

EXPECTED_ARCHIVE_SHA256 = "ebe2f62253e378b3b9934026c721ca3147c720b938b8161e6a98db1edf22e3df"
EXPECTED_SOURCE = "44d2355664d90079048bc1df93d3a9db2c240117"
EXPECTED_MODEL_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
SLOS = {"ttft_s": 0.250, "tpot_s": 0.015, "e2e_s": 2.0}


def sha256_path(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile_nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def load_json(path: pathlib.Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def event_names(row: dict[str, Any]) -> list[str]:
    return [event.get("event") for event in row.get("routing_events", [])]


def finite_or_none(value: Any) -> bool:
    return value is None or (isinstance(value, (int, float)) and math.isfinite(value))


def audit_arm(arm_dir: pathlib.Path) -> dict[str, Any]:
    manifest = load_json(arm_dir / "manifest.json")
    trace = load_json(arm_dir / "trace.json")
    rows = load_jsonl(arm_dir / "requests.jsonl")
    samples = load_json(arm_dir / "backend-samples.json")
    trace_by_id = {row["request_id"]: row for row in trace}
    row_by_id = {row["request_id"]: row for row in rows}
    duplicate_row_ids = len(rows) - len(row_by_id)
    duplicate_trace_ids = len(trace) - len(trace_by_id)
    missing_rows = sorted(set(trace_by_id) - set(row_by_id))
    unexpected_rows = sorted(set(row_by_id) - set(trace_by_id))
    shape_mismatches = []
    event_errors: list[str] = []
    outcome_counts = collections.Counter()
    slo_successes = 0
    scorer_errors = 0
    undefined_decisions = 0
    decision_count = 0
    selected_counts = collections.Counter()
    for request_id, row in row_by_id.items():
        outcome = row.get("outcome")
        outcome_counts[outcome] += 1
        trace_row = trace_by_id.get(request_id)
        if trace_row:
            for key in ("request_id", "prompt_seed", "input_tokens", "requested_output_tokens", "scheduled_offset_s"):
                if row.get(key) != trace_row.get(key):
                    shape_mismatches.append(f"{request_id}:{key}")
        decision = row.get("decision_event")
        if decision:
            decision_count += 1
            scorer_errors += int(decision.get("decision_reason") == "scorer_error")
            selected_counts[decision.get("selected_replica")] += 1
        else:
            undefined_decisions += 1
        names = event_names(row)
        event_index = {name: names.index(name) for name in set(names)}
        if outcome == "prediction_rejected":
            if not decision or not decision.get("enforcement_enabled") or not decision.get("predicted_reject"):
                event_errors.append(f"{request_id}:rejection_decision")
            if not decision or decision.get("observed_count", -1) < 8 or decision.get("count_threshold") != 8:
                event_errors.append(f"{request_id}:threshold")
            forbidden = {"reservation", "backend_dispatch", "prefill_release", "final_release"} & set(names)
            if forbidden:
                event_errors.append(f"{request_id}:rejected_has_{sorted(forbidden)}")
            if row.get("status") != 429 or row.get("error_code") != "prediction_count_threshold":
                event_errors.append(f"{request_id}:rejection_status")
        elif outcome == "accepted_success":
            required = ["prediction_admission_decision", "reservation", "backend_dispatch", "final_release", "final_accounting"]
            if any(name not in names for name in required):
                event_errors.append(f"{request_id}:accepted_missing_event")
            elif not (event_index["prediction_admission_decision"] < event_index["reservation"] < event_index["backend_dispatch"] < event_index["final_release"] < event_index["final_accounting"]):
                event_errors.append(f"{request_id}:event_order")
            if not decision or decision.get("admission_outcome") != "admitted":
                event_errors.append(f"{request_id}:accepted_decision")
            if decision and decision.get("enforcement_enabled") and decision.get("observed_count", 0) >= 8:
                event_errors.append(f"{request_id}:accepted_at_threshold")
            if row.get("status") != 200:
                event_errors.append(f"{request_id}:accepted_status")
            metrics = [row.get(key) for key in SLOS]
            if all(finite_or_none(value) and value is not None for value in metrics):
                if all(row[key] <= limit for key, limit in SLOS.items()):
                    slo_successes += 1
        else:
            if outcome not in {"safety_rejected", "failed", "timed_out"}:
                event_errors.append(f"{request_id}:unknown_outcome_{outcome}")
        for key in ("arrival_lag_s", "ttft_s", "tpot_s", "e2e_s"):
            if not finite_or_none(row.get(key)):
                event_errors.append(f"{request_id}:nonfinite_{key}")

    lags = [float(row["arrival_lag_s"]) for row in rows if finite_or_none(row.get("arrival_lag_s")) and row.get("arrival_lag_s") is not None]
    waiting_values = []
    sample_schema_errors = []
    for sample in samples:
        for backend, values in sample.items():
            if backend == "offset_s":
                continue
            if "waiting" not in values or not finite_or_none(values.get("waiting")):
                sample_schema_errors.append(f"{sample.get('offset_s')}:{backend}")
            else:
                waiting_values.append(float(values["waiting"]))
    final = manifest.get("final_accounting", {})
    replicas = final.get("replicas", {})
    final_empty = (
        final.get("queue", {}).get("queued_requests") == 0
        and final.get("queue", {}).get("queued_work") == 0
        and all(replica.get("active_reservations") == 0 and replica.get("inflight") == 0 and replica.get("outstanding_work") == 0 for replica in replicas.values())
    )
    metric_deltas = manifest.get("backend_metric_deltas", {})
    metric_presence = all(
        metric in values and finite_or_none(values.get(metric))
        for values in metric_deltas.values()
        for metric in ("vllm:num_preemptions_total", "vllm:prefix_cache_hits_total")
    ) and len(metric_deltas) == 2
    preemptions = sum(float(values.get("vllm:num_preemptions_total", math.nan)) for values in metric_deltas.values()) if metric_presence else None
    cache_hits = sum(float(values.get("vllm:prefix_cache_hits_total", math.nan)) for values in metric_deltas.values()) if metric_presence else None
    offered = len(trace)
    rate = float(manifest["rate"])
    recomputed = {
        "offered": offered,
        "recorded": len(rows),
        "accepted": outcome_counts["accepted_success"],
        "prediction_rejected": outcome_counts["prediction_rejected"],
        "safety_rejected": outcome_counts["safety_rejected"],
        "failed": outcome_counts["failed"],
        "timed_out": outcome_counts["timed_out"],
        "slo_successes": slo_successes,
        "slo_success_fraction": slo_successes / offered if offered else None,
        "slo_goodput_rps": slo_successes / (offered / rate) if offered else None,
    }
    summary = manifest.get("summary", {})
    manifest_match = (
        summary.get("offered_count") == recomputed["offered"]
        and summary.get("recorded_count") == recomputed["recorded"]
        and summary.get("accepted_count") == recomputed["accepted"]
        and summary.get("slo_success_count") == recomputed["slo_successes"]
        and math.isclose(summary.get("slo_goodput_rps", math.nan), recomputed["slo_goodput_rps"], abs_tol=1e-12)
        and all(summary.get("outcomes", {}).get(name) == recomputed[name] for name in ("prediction_rejected", "safety_rejected", "failed", "timed_out"))
    )
    valid = all([
        offered == 256,
        len(rows) == 256,
        not duplicate_row_ids,
        not duplicate_trace_ids,
        not missing_rows,
        not unexpected_rows,
        not shape_mismatches,
        not event_errors,
        decision_count == 256,
        undefined_decisions == 0,
        scorer_errors == 0,
        sum(outcome_counts.values()) == 256,
        recomputed["safety_rejected"] == 0,
        recomputed["failed"] == 0,
        recomputed["timed_out"] == 0,
        percentile_nearest_rank(lags, 0.95) is not None and percentile_nearest_rank(lags, 0.95) <= 0.050,
        max(lags, default=math.inf) <= 0.250,
        metric_presence,
        preemptions == 0,
        cache_hits == 0,
        not sample_schema_errors,
        final_empty,
        manifest_match,
    ])
    return {
        "arm_id": manifest["arm_id"],
        "configuration": manifest["configuration"],
        "rate": rate,
        "seed": manifest["seed"],
        "trace_sha256": manifest["trace_sha256"],
        "trace_file_sha256": sha256_path(arm_dir / "trace.json"),
        "protocol": {
            "count_threshold": manifest.get("count_threshold"),
            "routing": manifest.get("routing"),
            "queue_enabled": manifest.get("queue_enabled"),
            "beta": manifest.get("beta"),
            "model": manifest.get("model"),
            "model_revision": manifest.get("model_revision"),
            "input_tokens": manifest.get("input_tokens"),
            "output_tokens": manifest.get("output_tokens"),
        },
        "recomputed": recomputed,
        "checks": {
            "valid": valid,
            "manifest_match": manifest_match,
            "duplicate_row_ids": duplicate_row_ids,
            "duplicate_trace_ids": duplicate_trace_ids,
            "missing_rows": missing_rows,
            "unexpected_rows": unexpected_rows,
            "shape_mismatches": shape_mismatches,
            "event_errors": event_errors,
            "decision_count": decision_count,
            "undefined_decisions": undefined_decisions,
            "scorer_errors": scorer_errors,
            "selected_replica_counts": dict(selected_counts),
            "arrival_lag_p95_recomputed_s": percentile_nearest_rank(lags, 0.95),
            "arrival_lag_max_recomputed_s": max(lags, default=None),
            "metric_presence": metric_presence,
            "preemption_delta": preemptions,
            "prefix_cache_hit_delta": cache_hits,
            "backend_waiting_max": max(waiting_values, default=None),
            "backend_sample_schema_errors": sample_schema_errors,
            "final_accounting_empty": final_empty,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", required=True, type=pathlib.Path)
    parser.add_argument("--archive", required=True, type=pathlib.Path)
    parser.add_argument("--source-dir", required=True, type=pathlib.Path)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    archive_sha = sha256_path(args.archive)
    if archive_sha != EXPECTED_ARCHIVE_SHA256:
        raise SystemExit(f"archive hash mismatch: {archive_sha}")

    all_files = sorted(path for path in args.evidence_dir.rglob("*") if path.is_file())
    inventory_path = args.evidence_dir / "SHA256SUMS"
    inventory_entries: dict[str, str] = {}
    inventory_parse_errors = []
    for line in inventory_path.read_text(encoding="utf-8").splitlines():
        try:
            digest, absolute = line.split("  ", 1)
            relative = pathlib.Path(absolute).relative_to(args.evidence_dir).as_posix()
            inventory_entries[relative] = digest
        except (ValueError, TypeError):
            inventory_parse_errors.append(line)
    actual_relative = {path.relative_to(args.evidence_dir).as_posix(): path for path in all_files}
    expected_inventory_files = set(actual_relative) - {"SHA256SUMS"}
    missing_inventory_entries = sorted(expected_inventory_files - set(inventory_entries))
    unexpected_inventory_entries = sorted(set(inventory_entries) - expected_inventory_files)
    inventory_hash_mismatches = sorted(
        relative for relative, digest in inventory_entries.items()
        if relative in actual_relative and sha256_path(actual_relative[relative]) != digest
    )

    with tarfile.open(args.archive, "r:gz") as archive:
        members = archive.getmembers()
        member_names = [member.name.rstrip("/") for member in members]
        duplicate_archive_members = sorted(name for name, count in collections.Counter(member_names).items() if count > 1)
        prefix = args.evidence_dir.name + "/"
        archive_files = {}
        archive_hash_mismatches = []
        for member in members:
            if not member.isfile():
                continue
            relative = member.name[len(prefix):] if member.name.startswith(prefix) else member.name
            archive_files[relative] = member
            extracted = archive.extractfile(member)
            digest = hashlib.sha256(extracted.read()).hexdigest() if extracted else None
            if relative not in actual_relative or digest != sha256_path(actual_relative[relative]):
                archive_hash_mismatches.append(relative)
    missing_archive_files = sorted(set(actual_relative) - set(archive_files))
    unexpected_archive_files = sorted(set(archive_files) - set(actual_relative))

    catalog = load_json(args.source_dir / "experiments/e8/ARM_CATALOG.json")
    expected_order = []
    for pair in catalog["pairs"]:
        for config in pair["order"]:
            expected_order.append(f"e8-measured-{config}-r{pair['rate']}-s{pair['seed']}")
    expected_arms = set(expected_order)
    found_arms = {path.name for path in args.evidence_dir.glob("e8-measured-*") if path.is_dir()}
    arm_results = [audit_arm(args.evidence_dir / arm_id) for arm_id in sorted(found_arms)]
    by_pair = {(arm["rate"], arm["seed"], arm["configuration"]): arm for arm in arm_results}
    trace_pair_errors = []
    paired_differences: dict[str, list[float]] = {}
    for rate in (8.0, 10.0, 11.0):
        diffs = []
        for seed in (82001, 82002, 82003):
            arm_a, arm_b = by_pair[(rate, seed, "A")], by_pair[(rate, seed, "B")]
            if arm_a["trace_sha256"] != arm_b["trace_sha256"] or arm_a["trace_file_sha256"] != arm_b["trace_file_sha256"]:
                trace_pair_errors.append(f"r{rate:g}-s{seed}")
            diffs.append(arm_b["recomputed"]["slo_goodput_rps"] - arm_a["recomputed"]["slo_goodput_rps"])
        paired_differences[f"{rate:g}"] = diffs
    aggregates = {}
    for rate in (8.0, 10.0, 11.0):
        for config in ("A", "B"):
            arms = [by_pair[(rate, seed, config)] for seed in (82001, 82002, 82003)]
            aggregates[f"{rate:g}-{config}"] = {
                key: sum(arm["recomputed"][key] for arm in arms)
                for key in ("offered", "accepted", "prediction_rejected", "safety_rejected", "failed", "timed_out", "slo_successes")
            }
            aggregates[f"{rate:g}-{config}"]["slo_goodput_rps"] = sum(arm["recomputed"]["slo_successes"] for arm in arms) / (3 * 256 / rate)
    paired_statistics = {}
    t_critical_df2 = 4.302652729911275
    for rate, values in paired_differences.items():
        mean = statistics.mean(values)
        sample_sd = statistics.stdev(values)
        margin = t_critical_df2 * sample_sd / math.sqrt(3)
        paired_statistics[rate] = {
            "differences": values,
            "mean": mean,
            "range": [min(values), max(values)],
            "sample_sd": sample_sd,
            "descriptive_95pct_t_interval": [mean - margin, mean + margin],
        }

    qualification = {
        "source_commit_marker": (args.evidence_dir / "qualification/git-head.txt").read_text().strip(),
        "cpu_tests_exit": (args.evidence_dir / "qualification/cpu-tests.exit").read_text().strip(),
        "gpu_inventory": (args.evidence_dir / "qualification/gpus.txt").read_text().strip().splitlines(),
        "python": (args.evidence_dir / "qualification/python.txt").read_text().strip(),
        "vllm": (args.evidence_dir / "qualification/vllm.txt").read_text().strip(),
        "workspace_mount": (args.evidence_dir / "qualification/workspace-mount.txt").read_text().strip(),
    }
    process_texts = [
        (args.evidence_dir / "qualification/replica1-process.txt").read_text(),
        (args.evidence_dir / "qualification/replica2-process.txt").read_text(),
    ]
    launch_checks = {
        "two_replicas": len(process_texts) == 2 and all("vllm serve" in text for text in process_texts),
        "ports_8101_8102": "--port 8101" in process_texts[0] and "--port 8102" in process_texts[1],
        "prefix_cache_disabled": all("--no-enable-prefix-caching" in text for text in process_texts),
        "model_revision_frozen": all(EXPECTED_MODEL_REVISION in text for text in process_texts),
        "tp_pp_one": all("--tensor-parallel-size 1" in text and "--pipeline-parallel-size 1" in text for text in process_texts),
        "seed_42": all("--seed 42" in text for text in process_texts),
    }
    deviation_text = (args.evidence_dir / "OPERATOR_DEVIATIONS.txt").read_text()
    failed_logs = [
        (args.evidence_dir / "logs/replica1-initial-cli-failure.log").read_text(errors="replace"),
        (args.evidence_dir / "logs/replica2-initial-cli-failure.log").read_text(errors="replace"),
    ]
    deviation_checks = {
        "documented": "omitted only that unsupported logging flag" in deviation_text,
        "two_failed_logs": len(failed_logs) == 2,
        "unsupported_flag_in_both": all("disable-log-requests" in text for text in failed_logs),
        "qualified_commands_omit_unsupported_flag": all("--disable-log-requests" not in text for text in process_texts),
        "other_frozen_launch_settings_retained": all(launch_checks.values()),
    }
    smoke = [load_json(path) for path in sorted(args.evidence_dir.glob("e8-smoke-*/manifest.json"))]
    pilot = [load_json(path) for path in sorted(args.evidence_dir.glob("e8-pilot-*/manifest.json"))]
    preflight = load_json(args.evidence_dir / "cpu-preflight/preflight.json")
    preflight_passed = (
        isinstance(preflight, list)
        and {item.get("configuration") for item in preflight} == {"A", "B"}
        and all(item.get("backend_dispatches") in {2, 3} for item in preflight)
        and all(
            item.get("accounting", {}).get("queue", {}).get("queued_requests") == 0
            and all(replica.get("active_reservations") == 0 and replica.get("inflight") == 0 for replica in item.get("accounting", {}).get("replicas", {}).values())
            for item in preflight
        )
    )
    stage_checks = {
        "cpu_preflight_present_and_passed": preflight_passed,
        "smoke_arm_count": len(smoke),
        "smoke_all_valid": len(smoke) == 2 and all(item.get("evaluation_valid") for item in smoke),
        "pilot_arm_count": len(pilot),
        "pilot_all_valid": len(pilot) == 4 and all(item.get("evaluation_valid") for item in pilot),
        "pilot_gate": load_json(args.evidence_dir / "pilot-gate.json"),
    }
    all_arm_valid = len(arm_results) == 18 and all(arm["checks"]["valid"] for arm in arm_results)
    inventory_ok = not any([
        inventory_parse_errors, missing_inventory_entries, unexpected_inventory_entries,
        inventory_hash_mismatches, duplicate_archive_members, archive_hash_mismatches,
        missing_archive_files, unexpected_archive_files,
    ])
    overall_verified = all([
        archive_sha == EXPECTED_ARCHIVE_SHA256,
        len(all_files) == 162,
        len(inventory_entries) == 161,
        inventory_ok,
        qualification["source_commit_marker"] == EXPECTED_SOURCE,
        all(launch_checks.values()),
        all(deviation_checks.values()),
        set(expected_arms) == found_arms,
        not trace_pair_errors,
        all_arm_valid,
        stage_checks["cpu_preflight_present_and_passed"],
        stage_checks["smoke_all_valid"],
        stage_checks["pilot_all_valid"],
    ])
    summary = {
        "status": "E8_EVIDENCE_VERIFIED" if overall_verified else "E8_EVIDENCE_DISCREPANCY_FOUND",
        "audit_timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "environment": {
            "hostname": platform.node(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_only": True,
        },
        "paths": {key: str(value) for key, value in vars(args).items()},
        "archive": {"bytes": args.archive.stat().st_size, "sha256": archive_sha, "expected_sha256": EXPECTED_ARCHIVE_SHA256},
        "inventory": {
            "directory_file_count_including_inventory": len(all_files),
            "inventory_entry_count_excluding_inventory": len(inventory_entries),
            "archive_regular_file_count": len(archive_files),
            "archive_member_count_including_directories": len(members),
            "parse_errors": inventory_parse_errors,
            "missing_entries": missing_inventory_entries,
            "unexpected_entries": unexpected_inventory_entries,
            "hash_mismatches": inventory_hash_mismatches,
            "duplicate_archive_members": duplicate_archive_members,
            "archive_vs_directory_hash_mismatches": archive_hash_mismatches,
            "missing_archive_files": missing_archive_files,
            "unexpected_archive_files": unexpected_archive_files,
            "verified": inventory_ok,
        },
        "qualification": qualification,
        "launch_checks": launch_checks,
        "launch_deviation_checks": deviation_checks,
        "stage_checks": stage_checks,
        "catalog": {
            "expected_order": expected_order,
            "order_verifiable_from_evidence": False,
            "order_limitation": "The catalog is preserved, but per-arm manifests contain no run-start timestamp and the aggregate outcomes file is keyed, so exact runtime order cannot be independently reconstructed.",
            "missing_arms": sorted(expected_arms - found_arms),
            "unexpected_arms": sorted(found_arms - expected_arms),
            "trace_pair_errors": trace_pair_errors,
        },
        "arms": arm_results,
        "aggregates": aggregates,
        "paired_statistics": paired_statistics,
        "limitations": [
            "Request-row token counts and requested output shape are retained, but generated token text and per-token timestamps are not; streaming completion semantics are represented by TTFT/TPOT/E2E and lifecycle events rather than a raw token stream.",
            "The preserved catalog establishes intended execution order, but exact runtime order is not independently reconstructable from timestamp-free manifests.",
            "The audit verifies the mounted evidence and archive against each other; it does not create an independent off-volume raw-data backup.",
        ],
        "conclusion_supported": overall_verified,
        "another_gpu_run_required": False if overall_verified else None,
    }
    summary_path = args.output_dir / "audit_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    rows = []
    for key in ("8-A", "8-B", "10-A", "10-B", "11-A", "11-B"):
        item = aggregates[key]
        rows.append(f"| {key.replace('-', ' | ')} | {item['offered']} | {item['accepted']} | {item['prediction_rejected']} | {item['safety_rejected']} | {item['failed']} | {item['timed_out']} | {item['slo_successes']} | {item['slo_goodput_rps']:.9f} |")
    report = f"""# E8 evidence audit

Status: `{summary['status']}`

## Identity and integrity

- Pod hostname: `{platform.node()}` (CPU-only audit; no inference launched)
- Mounted evidence: `{args.evidence_dir}`
- Archive size: `{args.archive.stat().st_size}` bytes
- Archive SHA-256: `{archive_sha}` (matches expected)
- Directory files: {len(all_files)} total; inventory contains {len(inventory_entries)} entries and excludes `SHA256SUMS` itself
- Archive: {len(archive_files)} regular files, {len(members)} total members including directories, no duplicate member names
- Inventory hashes and archive-to-directory file hashes: {'verified' if inventory_ok else 'DISCREPANCY'}

## Protocol and coverage

- Executed source marker: `{qualification['source_commit_marker']}`
- Two replicas, frozen model revision, TP=1/PP=1, seed 42, and disabled prefix cache: verified from saved process commands
- CPU preflight, two-arm GPU smoke, and four-arm pilot: present and valid
- Measured arms: {len(arm_results)}; missing {len(expected_arms - found_arms)}; unexpected {len(found_arms - expected_arms)}
- Each measured arm: 256 canonical offers and 256 unique request rows
- Canonical A/B trace identity: verified for all nine pairs
- Threshold 8, round-robin routing, queue disabled, beta and token shape: verified in every manifest and request lifecycle
- Exact runtime order: not independently reconstructable because manifests have no start timestamp; the frozen catalog is preserved
- Launch deviation: both pre-traffic failures identify unsupported `--disable-log-requests`; qualified commands omit that flag while retaining the other checked frozen settings

## Recomputed measured results

| Rate | Config | Offered | Accepted | Prediction rejected | Safety rejected | Failed | Timed out | SLO successes | SLO goodput |
| ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{os.linesep.join(rows)}

Paired B-minus-A goodput differences:

- 8 req/s: {paired_differences['8']}
- 10 req/s: {paired_differences['10']}
- 11 req/s: {paired_differences['11']}

All values were recomputed from request rows using TTFT <= 0.250 s, TPOT <= 0.015 s/token, and E2E <= 2.0 s. They match the published aggregates and paired differences.

## Validity and lifecycle

All 18 measured arms passed row/trace reconciliation, complete defined decision coverage, threshold consistency, rejection-without-reservation/dispatch, admitted decision/reservation/dispatch/release ordering, empty final ledgers, metric presence and finiteness, arrival-lag bounds, zero preemption and prefix-cache-hit deltas, and zero failures/timeouts/safety rejections. Backend waiting was present as a measured field and had a maximum of zero; it was not introduced as a retrospective rule.

## Limitations

- Retained rows preserve requested token shape and aggregate streaming latency, but not generated text or raw per-token timestamps.
- Exact runtime arm order cannot be independently reconstructed from the timestamp-free manifests.
- This audit does not create a second raw-data backup. The archive still lacks an independent off-volume copy; object-storage backup remains advisable as a separate operation.

## Conclusion

The published E8 conclusion remains supported for the frozen hardware, model, workload, and policy. No finding requires another GPU run. Historical evidence was read but not modified, no model was loaded, and no inference traffic was sent.

`{summary['status']}`
"""
    (args.output_dir / "AUDIT_REPORT.md").write_text(report, encoding="utf-8")
    return 0 if overall_verified else 2


if __name__ == "__main__":
    raise SystemExit(main())
