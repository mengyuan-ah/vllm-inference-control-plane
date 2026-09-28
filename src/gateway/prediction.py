from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from .tracker import Reservation


@dataclass(frozen=True)
class ShadowPredictionConfig:
    version: str
    count_threshold: int | None = None
    work_threshold: float | None = None


@dataclass(frozen=True)
class CountAdmissionPolicy:
    threshold: int = 8
    version: str = "e8-count-admission-v1"

    def decide(self, features: dict[str, Any]) -> dict[str, object]:
        decided_at = time.monotonic()
        count = features.get("selected_inflight_before")
        feature_timestamp = features.get("feature_timestamp")
        valid = (
            isinstance(count, int)
            and not isinstance(count, bool)
            and count >= 0
            and isinstance(feature_timestamp, (int, float))
            and not isinstance(feature_timestamp, bool)
            and math.isfinite(float(feature_timestamp))
            and 0 <= float(feature_timestamp) <= decided_at
            and decided_at - float(feature_timestamp) <= 1.0
        )
        return {
            "event": "prediction_admission_decision",
            "decision_timestamp": decided_at,
            "feature_timestamp": feature_timestamp,
            "observed_count": count,
            "count_threshold": self.threshold,
            "predicted_reject": count >= self.threshold if valid else None,
            "decision_reason": None if valid else "invalid_or_stale_count_features",
            "policy_version": self.version,
        }


class ShadowPredictor:
    """Pure shadow scorer: it records decisions and cannot affect dispatch."""

    def __init__(self, config: ShadowPredictionConfig):
        self.config = config

    def score(self, reservation: Reservation) -> dict[str, object]:
        started = time.perf_counter_ns()
        scored_at = time.monotonic()
        features = dict(reservation.prediction_features)
        count = features.get("selected_inflight_before")
        work = features.get("selected_outstanding_work_before")
        feature_timestamp = features.get("feature_timestamp")
        count_valid = isinstance(count, int) and not isinstance(count, bool) and count >= 0
        work_valid = (
            isinstance(work, (int, float))
            and not isinstance(work, bool)
            and math.isfinite(float(work))
            and work >= 0
        )
        timestamp_valid = (
            isinstance(feature_timestamp, (int, float))
            and not isinstance(feature_timestamp, bool)
            and math.isfinite(float(feature_timestamp))
            and 0 <= float(feature_timestamp) <= scored_at
        )
        feature_missing = not all(
            key in features
            for key in (
                "feature_timestamp",
                "selected_inflight_before",
                "selected_outstanding_work_before",
            )
        )
        feature_invalid = not (count_valid and work_valid and timestamp_valid)
        features_stale = bool(not feature_invalid and scored_at - float(feature_timestamp) > 1.0)
        feature_reason = (
            "missing_features"
            if feature_missing
            else "malformed_features"
            if feature_invalid
            else "stale_features"
            if features_stale
            else None
        )
        count_decision = (
            None
            if feature_invalid or features_stale or self.config.count_threshold is None
            else count >= self.config.count_threshold
        )
        work_decision = (
            None
            if feature_invalid or features_stale or self.config.work_threshold is None
            else float(work) >= self.config.work_threshold
        )
        return {
            "event": "shadow_prediction",
            "request_id": reservation.request_id,
            "selected_replica": reservation.replica_id,
            "prediction_timestamp": scored_at,
            "feature_timestamp": feature_timestamp,
            "selected_inflight_before": count,
            "selected_outstanding_work_before": work,
            "features_missing": feature_missing,
            "features_invalid": feature_invalid,
            "features_stale": features_stale,
            "predictor_version": self.config.version,
            "parameters": {
                "count_threshold": self.config.count_threshold,
                "work_threshold": self.config.work_threshold,
            },
            "predictions": {
                "always_pass": False,
                "count_threshold": count_decision,
                "work_threshold": work_decision,
            },
            "prediction_reasons": {
                "always_pass": None,
                "count_threshold": feature_reason
                or ("threshold_not_configured" if self.config.count_threshold is None else None),
                "work_threshold": feature_reason
                or ("threshold_not_configured" if self.config.work_threshold is None else None),
            },
            "computation_ns": time.perf_counter_ns() - started,
        }


def undefined_prediction(
    reservation: Reservation, *, predictor_version: str, error: Exception
) -> dict[str, object]:
    """Build a non-throwing shadow event when the scorer itself fails."""
    features = reservation.prediction_features
    features_missing = not all(
        key in features
        for key in (
            "feature_timestamp",
            "selected_inflight_before",
            "selected_outstanding_work_before",
        )
    )
    return {
        "event": "shadow_prediction",
        "request_id": reservation.request_id,
        "selected_replica": reservation.replica_id,
        "prediction_timestamp": time.monotonic(),
        "feature_timestamp": features.get("feature_timestamp"),
        "selected_inflight_before": features.get("selected_inflight_before"),
        "selected_outstanding_work_before": features.get("selected_outstanding_work_before"),
        "features_missing": features_missing,
        "features_invalid": None,
        "features_stale": None,
        "predictor_version": predictor_version,
        "prediction_error": type(error).__name__,
        "parameters": {},
        "predictions": {
            "always_pass": None,
            "count_threshold": None,
            "work_threshold": None,
        },
        "prediction_reasons": {
            "always_pass": f"scorer_error:{type(error).__name__}",
            "count_threshold": f"scorer_error:{type(error).__name__}",
            "work_threshold": f"scorer_error:{type(error).__name__}",
        },
        "computation_ns": None,
    }
