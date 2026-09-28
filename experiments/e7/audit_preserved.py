#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import tarfile
from collections import defaultdict
from pathlib import Path
from typing import Any

EXPECTED_ARCHIVE_SHA256 = "42d069601f0e88de556c7b89c3ba597884111b1739f2eeb6bf40a947006ed1a7"
SLO_LIMITS = {"ttft_s": 0.250, "tpot_s": 0.015, "e2e_s": 2.0}
PREDICTORS = ("always_pass", "count_threshold", "work_threshold")
MEASURED_ARM = re.compile(r"e7-measured-r(?P<rate>8|10|11)-s(?P<seed>72001|72002|72003)$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def confusion(rows: list[dict[str, Any]], predictor: str) -> dict[str, int]:
    return {
        "tp": sum(row["predictions"][predictor] is True and row["slo_miss"] for row in rows),
        "fp": sum(row["predictions"][predictor] is True and not row["slo_miss"] for row in rows),
        "fn": sum(row["predictions"][predictor] is False and row["slo_miss"] for row in rows),
        "tn": sum(row["predictions"][predictor] is False and not row["slo_miss"] for row in rows),
    }


def audit(archive: Path) -> dict[str, Any]:
    archive_sha256 = sha256_file(archive)
    if archive_sha256 != EXPECTED_ARCHIVE_SHA256:
        raise ValueError(
            f"archive SHA-256 mismatch: expected {EXPECTED_ARCHIVE_SHA256}, got {archive_sha256}"
        )

    discrepancies: list[str] = []
    with tarfile.open(archive, "r:gz") as bundle:
        members = {member.name: member for member in bundle.getmembers() if member.isfile()}
        roots = {name.split("/", 1)[0] for name in members}
        if len(roots) != 1:
            raise ValueError(f"expected one archive root, found {sorted(roots)}")
        root = roots.pop()

        def read_bytes(relative: str) -> bytes:
            extracted = bundle.extractfile(members[f"{root}/{relative}"])
            if extracted is None:
                raise ValueError(f"cannot read {relative}")
            return extracted.read()

        def read_json(relative: str) -> Any:
            return json.loads(read_bytes(relative))

        inventory_failures: list[str] = []
        inventory = read_bytes("FINAL_INVENTORY.tsv").decode().splitlines()
        for line in inventory:
            _, expected_size, expected_sha, relative = line.split("\t")
            relative = relative.removeprefix("./")
            payload = read_bytes(relative)
            actual_sha = hashlib.sha256(payload).hexdigest()
            if len(payload) != int(expected_size) or actual_sha != expected_sha:
                inventory_failures.append(relative)

        frozen = read_json("FROZEN_MEASURED.json")
        parameters = read_json("predictor-parameters.json")
        names = {name.rsplit("/", 1)[0].split("/")[-1] for name in members}
        measured_names = sorted(name for name in names if name.startswith("e7-measured-"))
        calibration_names = sorted(name for name in names if name.startswith("e7-calibration-"))
        expected_combinations = {
            (float(rate), seed) for rate in frozen["rates_rps"] for seed in frozen["seeds"]
        }
        actual_combinations: set[tuple[float, int]] = set()
        all_rows: list[dict[str, Any]] = []
        rows_by_rate: dict[float, list[dict[str, Any]]] = defaultdict(list)
        arm_row_counts: dict[str, int] = {}
        feature_failures = 0
        parameter_failures = 0
        prediction_failures = 0
        label_failures = 0
        event_failures = 0
        prediction_before_dispatch_supported = 0

        for arm in measured_names:
            match = MEASURED_ARM.fullmatch(arm)
            if match is None:
                discrepancies.append(f"unexpected measured arm name: {arm}")
                continue
            rate, seed = float(match.group("rate")), int(match.group("seed"))
            if (rate, seed) in actual_combinations:
                discrepancies.append(f"duplicate measured rate/seed: {rate}/{seed}")
            actual_combinations.add((rate, seed))
            summary = read_json(f"{arm}/summary.json")
            rows = read_json(f"{arm}/requests.json")
            events = read_json(f"{arm}/gateway-events.json")
            arm_row_counts[arm] = len(rows)
            if summary.get("stage") != "measured":
                discrepancies.append(f"{arm}: stage is not measured")
            if summary.get("configured_offered_rate_rps") != rate:
                discrepancies.append(f"{arm}: configured rate does not match arm name")
            if len(rows) != frozen["requests_per_arm"]:
                discrepancies.append(f"{arm}: found {len(rows)} request rows")
            for row in rows:
                expected_parameters = {
                    "count_threshold": parameters["count_threshold"],
                    "work_threshold": parameters["work_threshold"],
                }
                if (
                    row.get("predictor_parameters") != expected_parameters
                    or row.get("predictor_version") != parameters["predictor_version"]
                ):
                    parameter_failures += 1
                feature_timestamp = row.get("feature_timestamp")
                prediction_timestamp = row.get("prediction_timestamp")
                count = row.get("selected_inflight_before")
                work = row.get("selected_outstanding_work_before")
                features_valid = (
                    isinstance(count, int)
                    and not isinstance(count, bool)
                    and count >= 0
                    and isinstance(work, (int, float))
                    and not isinstance(work, bool)
                    and math.isfinite(float(work))
                    and work >= 0
                    and isinstance(feature_timestamp, (int, float))
                    and math.isfinite(float(feature_timestamp))
                    and isinstance(prediction_timestamp, (int, float))
                    and math.isfinite(float(prediction_timestamp))
                    and 0 <= prediction_timestamp - feature_timestamp <= 1.0
                )
                if not features_valid:
                    feature_failures += 1
                expected_predictions = {
                    "always_pass": False,
                    "count_threshold": count >= parameters["count_threshold"],
                    "work_threshold": float(work) >= parameters["work_threshold"],
                }
                if row.get("predictions") != expected_predictions:
                    prediction_failures += 1
                expected_labels = {
                    f"{metric.removesuffix('_s')}_miss": row[metric] > limit
                    for metric, limit in SLO_LIMITS.items()
                }
                expected_labels["slo_miss"] = any(expected_labels.values())
                if not row.get("success") or any(
                    row.get(label) is not value for label, value in expected_labels.items()
                ):
                    label_failures += 1
                request_events = events.get(row["request_id"], [])
                predictions = [
                    event for event in request_events if event.get("event") == "shadow_prediction"
                ]
                reservations = [
                    event for event in request_events if event.get("event") == "reservation"
                ]
                releases = [
                    event for event in request_events if event.get("event") == "final_release"
                ]
                if len(predictions) != 1 or len(reservations) != 1 or len(releases) != 1:
                    event_failures += 1
                elif request_events.index(reservations[0]) < request_events.index(predictions[0]):
                    prediction_before_dispatch_supported += 1
                else:
                    event_failures += 1
            all_rows.extend(rows)
            rows_by_rate[rate].extend(rows)

        for arm in calibration_names:
            if read_json(f"{arm}/summary.json").get("stage") != "calibration":
                discrepancies.append(f"{arm}: stage is not calibration")

    if actual_combinations != expected_combinations:
        discrepancies.append(
            "measured combinations differ: "
            f"missing={sorted(expected_combinations - actual_combinations)}, "
            f"extra={sorted(actual_combinations - expected_combinations)}"
        )
    if set(calibration_names) != set(parameters["calibration_arms"]):
        discrepancies.append("calibration arm set differs from predictor-parameter provenance")
    if feature_failures:
        discrepancies.append(f"{feature_failures} measured rows have invalid prediction features")
    if parameter_failures:
        discrepancies.append(f"{parameter_failures} measured rows have inconsistent parameters")
    if prediction_failures:
        discrepancies.append(f"{prediction_failures} measured rows fail predictor reproduction")
    if label_failures:
        discrepancies.append(f"{label_failures} measured rows fail SLO-label reproduction")
    if event_failures:
        discrepancies.append(f"{event_failures} measured rows fail event checks")

    by_rate = {
        str(int(rate)): {
            "requests": len(rows),
            "slo_misses": sum(row["slo_miss"] for row in rows),
            "healthy": sum(not row["slo_miss"] for row in rows),
            "predictors": {name: confusion(rows, name) for name in PREDICTORS},
        }
        for rate, rows in sorted(rows_by_rate.items())
    }
    count_work_identical = sum(
        row["predictions"]["count_threshold"] == row["predictions"]["work_threshold"]
        for row in all_rows
    )
    return {
        "archive": str(archive),
        "archive_sha256": archive_sha256,
        "archive_sha256_verified": True,
        "inventory_entries": len(inventory),
        "inventory_failures": inventory_failures,
        "frozen_source_commit": frozen["source_commit"],
        "measured_arms": measured_names,
        "measured_arm_count": len(measured_names),
        "requests_per_arm": arm_row_counts,
        "calibration_arms": calibration_names,
        "calibration_measured_separation": not set(calibration_names) & set(measured_names),
        "predictor_parameters": parameters,
        "total_requests": len(all_rows),
        "completed_requests": sum(row.get("success") is True for row in all_rows),
        "defined_prediction_coverage": {
            name: sum(isinstance(row["predictions"].get(name), bool) for row in all_rows)
            for name in PREDICTORS
        },
        "valid_feature_rows": len(all_rows) - feature_failures,
        "consistent_parameter_rows": len(all_rows) - parameter_failures,
        "reproduced_prediction_rows": len(all_rows) - prediction_failures,
        "reproduced_label_rows": len(all_rows) - label_failures,
        "event_valid_rows": len(all_rows) - event_failures,
        "reservation_before_prediction_rows": prediction_before_dispatch_supported,
        "timing_limitation": (
            "Saved event order and timestamps establish feature capture before prediction and "
            "reservation-event ordering before prediction. No backend-dispatch timestamp was "
            "saved, so raw evidence alone cannot independently prove prediction preceded backend "
            "dispatch; "
            "that ordering is established by the executed source."
        ),
        "overall": {
            "slo_misses": sum(row["slo_miss"] for row in all_rows),
            "healthy": sum(not row["slo_miss"] for row in all_rows),
            "predictors": {name: confusion(all_rows, name) for name in PREDICTORS},
            "count_work_identical_rows": count_work_identical,
        },
        "by_rate": by_rate,
        "discrepancies": discrepancies,
        "audit_passed": not discrepancies and not inventory_failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.archive)
    if args.output.exists():
        parser.error(f"refusing to overwrite {args.output}")
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"audit_passed": result["audit_passed"], "output": str(args.output)}))
    raise SystemExit(0 if result["audit_passed"] else 1)


if __name__ == "__main__":
    main()
