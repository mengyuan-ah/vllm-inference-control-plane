from __future__ import annotations

import asyncio

import pytest

from gateway.config import ReplicaConfig
from gateway.models import ReplicaLifecycle
from gateway.tracker import ReplicaTracker
from gateway.work import LinearWorkEstimator

FROZEN_BETA = 107.2820167


def replicas(max_inflight: int = 20):
    return (
        ReplicaConfig("r1", "http://r1", max_inflight=max_inflight, max_work=1_000_000),
        ReplicaConfig("r2", "http://r2", max_inflight=max_inflight, max_work=1_000_000),
    )


def test_frozen_beta_calculation_is_exact_expression() -> None:
    estimator = LinearWorkEstimator(1.0, FROZEN_BETA)
    assert estimator.estimate(1024, 128) == (1024 + FROZEN_BETA * 128, 1024)


@pytest.mark.asyncio
async def test_work_aware_selects_minimum_and_rotates_ties() -> None:
    tracker = ReplicaTracker(replicas(), "work_aware", FROZEN_BETA)
    first = await tracker.reserve(10)
    second = await tracker.reserve(3)
    third = await tracker.reserve(3)
    assert [first.replica_id, second.replica_id, third.replica_id] == ["r1", "r2", "r2"]
    await asyncio.gather(first.release(), second.release(), third.release())
    tied = [await tracker.reserve(1), await tracker.reserve(1)]
    assert [item.replica_id for item in tied] == ["r1", "r2"]
    await asyncio.gather(*(item.release() for item in tied))


@pytest.mark.asyncio
async def test_selection_and_reservation_are_atomic() -> None:
    tracker = ReplicaTracker(replicas(), "work_aware", FROZEN_BETA)
    reservations = await asyncio.gather(*(tracker.reserve(1) for _ in range(20)))
    snapshot = await tracker.snapshot()
    assert snapshot["r1"]["inflight"] == snapshot["r2"]["inflight"] == 10
    await asyncio.gather(*(item.release() for item in reservations))
    assert all(item["outstanding_work"] == 0 for item in (await tracker.snapshot()).values())


@pytest.mark.asyncio
async def test_ineligible_replica_is_not_a_candidate() -> None:
    events = []
    tracker = ReplicaTracker(replicas(), "work_aware", FROZEN_BETA)
    await tracker.set_lifecycle("r1", ReplicaLifecycle.UNHEALTHY)
    reservation = await tracker.reserve(2, request_id="excluded", events=events)
    assert reservation.replica_id == "r2"
    assert events[0]["eligible_replicas"] == ["r2"]
    await reservation.release()


@pytest.mark.asyncio
async def test_routing_event_reconstructs_tie_and_exactly_once_releases() -> None:
    events = []
    tracker = ReplicaTracker(replicas(), "work_aware", FROZEN_BETA)
    reservation = await tracker.reserve(
        1024 + FROZEN_BETA * 128,
        request_id="e5-1",
        metadata={"input_tokens": 1024, "requested_output_tokens": 128},
        events=events,
    )
    await reservation.first_content(1024)
    await reservation.first_content(1024)
    assert await reservation.release() is True
    assert await reservation.release() is False
    assert [event["event"] for event in events] == [
        "reservation",
        "prefill_release",
        "final_release",
    ]
    assert events[0]["frozen_beta"] == FROZEN_BETA
    assert events[0]["tie_candidates"] == ["r1", "r2"]
    assert events[-1]["outstanding_after"] == 0
