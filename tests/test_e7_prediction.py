from __future__ import annotations

import time

import pytest

from gateway.config import ReplicaConfig
from gateway.prediction import ShadowPredictionConfig, ShadowPredictor
from gateway.tracker import ReplicaTracker


@pytest.mark.asyncio
async def test_prediction_features_are_captured_atomically_before_dispatch() -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "unused", max_inflight=8),))
    events: list[dict[str, object]] = []
    first = await tracker.reserve(100, request_id="one")
    assert first is not None
    reservation = await tracker.reserve(100, request_id="two", events=events)
    assert reservation is not None
    predictor = ShadowPredictor(ShadowPredictionConfig("v1", 1, 50))
    prediction = predictor.score(reservation)
    assert prediction["selected_inflight_before"] == 1
    assert prediction["selected_outstanding_work_before"] == 100
    assert prediction["prediction_timestamp"] >= prediction["feature_timestamp"]
    await first.release()
    await reservation.release()


@pytest.mark.asyncio
async def test_missing_or_stale_features_yield_no_threshold_decision() -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "unused"),))
    reservation = await tracker.reserve(1)
    assert reservation is not None
    predictor = ShadowPredictor(ShadowPredictionConfig("v1", 0, 0))
    reservation.prediction_features = {}
    missing = predictor.score(reservation)
    assert missing["predictions"]["count_threshold"] is None
    assert missing["features_missing"] is True
    reservation.prediction_features = {
        "selected_inflight_before": 0,
        "selected_outstanding_work_before": 0,
    }
    missing_timestamp = predictor.score(reservation)
    assert missing_timestamp["predictions"]["count_threshold"] is None
    assert missing_timestamp["features_missing"] is True
    assert missing_timestamp["features_invalid"] is True
    assert missing_timestamp["prediction_reasons"]["count_threshold"] == "missing_features"
    reservation.prediction_features = {
        "feature_timestamp": time.monotonic() - 2,
        "selected_inflight_before": 0,
        "selected_outstanding_work_before": 0,
    }
    assert predictor.score(reservation)["predictions"]["work_threshold"] is None
    stale_reason = predictor.score(reservation)["prediction_reasons"]["work_threshold"]
    assert stale_reason == "stale_features"
    await reservation.release()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("selected_inflight_before", "zero"),
        ("selected_outstanding_work_before", float("nan")),
        ("feature_timestamp", float("inf")),
    ),
)
async def test_malformed_or_nonfinite_features_have_explicit_undefined_reason(
    field: str, value: object
) -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "unused"),))
    reservation = await tracker.reserve(1)
    assert reservation is not None
    reservation.prediction_features[field] = value
    prediction = ShadowPredictor(ShadowPredictionConfig("v1", 0, 0)).score(reservation)
    assert prediction["predictions"]["count_threshold"] is None
    assert prediction["predictions"]["work_threshold"] is None
    assert prediction["prediction_reasons"]["count_threshold"] == "malformed_features"
    assert prediction["prediction_reasons"]["work_threshold"] == "malformed_features"
    await reservation.release()


@pytest.mark.asyncio
async def test_scoring_does_not_change_routing_or_release_accounting() -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "unused"), ReplicaConfig("r2", "unused")))
    predictor = ShadowPredictor(ShadowPredictionConfig("v1", 0, 0))
    reservations = [await tracker.reserve(10), await tracker.reserve(10)]
    assert [item.replica_id for item in reservations if item] == ["r1", "r2"]
    for reservation in reservations:
        assert reservation is not None
        predictor.score(reservation)
        await reservation.release()
    assert all(
        row["inflight"] == 0 and row["outstanding_work"] == 0
        for row in (await tracker.snapshot()).values()
    )
