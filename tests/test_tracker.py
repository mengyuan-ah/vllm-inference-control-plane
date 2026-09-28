from __future__ import annotations

import asyncio

import pytest

from gateway.config import ReplicaConfig
from gateway.models import ReplicaLifecycle
from gateway.tracker import ReplicaTracker


def configs(**overrides):
    defaults = {"max_inflight": 2, "max_work": 10.0}
    defaults.update(overrides)
    return (
        ReplicaConfig("r1", "http://r1", **defaults),
        ReplicaConfig("r2", "http://r2", **defaults),
    )


@pytest.mark.asyncio
async def test_round_robin_and_cursor_only_moves_on_success() -> None:
    tracker = ReplicaTracker(configs(max_inflight=1))
    first = await tracker.reserve(1)
    second = await tracker.reserve(1)
    blocked_cursor = tracker.cursor
    assert [first.replica_id, second.replica_id] == ["r1", "r2"]
    assert await tracker.reserve(1) is None
    assert tracker.cursor == blocked_cursor
    await first.release()
    third = await tracker.reserve(1)
    assert third.replica_id == "r1"
    await second.release()
    await third.release()


@pytest.mark.asyncio
async def test_count_and_work_headroom() -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "x", max_inflight=1, max_work=5),))
    assert await tracker.reserve(6) is None
    reservation = await tracker.reserve(5)
    assert reservation is not None
    assert await tracker.reserve(1) is None
    await reservation.release()


@pytest.mark.asyncio
async def test_lifecycle_and_recovery_limits() -> None:
    tracker = ReplicaTracker(
        (
            ReplicaConfig(
                "r1",
                "x",
                max_inflight=4,
                max_work=100,
                recovery_max_inflight=1,
                recovery_max_work=3,
                degraded_penalty=1.5,
            ),
        )
    )
    for state, eligible in (
        (ReplicaLifecycle.HEALTHY, True),
        (ReplicaLifecycle.DEGRADED, True),
        (ReplicaLifecycle.UNHEALTHY, False),
        (ReplicaLifecycle.HALF_OPEN, False),
        (ReplicaLifecycle.RECOVERING, True),
    ):
        await tracker.set_lifecycle("r1", state)
        reservation = await tracker.reserve(2)
        assert (reservation is not None) is eligible
        if reservation:
            await reservation.release()
    await tracker.set_lifecycle("r1", ReplicaLifecycle.DEGRADED)
    assert tracker.replicas["r1"].penalty_metadata == 1.5
    await tracker.set_lifecycle("r1", ReplicaLifecycle.RECOVERING)
    assert await tracker.reserve(4) is None


@pytest.mark.asyncio
async def test_atomic_concurrent_reservation_and_no_negative_accounting() -> None:
    tracker = ReplicaTracker(configs(max_inflight=5, max_work=5))
    results = await asyncio.gather(*(tracker.reserve(1) for _ in range(50)))
    reservations = [item for item in results if item is not None]
    assert len(reservations) == 10
    snapshot = await tracker.snapshot()
    assert sum(item["inflight"] for item in snapshot.values()) == 10
    await asyncio.gather(*(item.release() for item in reservations))
    await asyncio.gather(*(item.release() for item in reservations))
    snapshot = await tracker.snapshot()
    assert all(
        item["inflight"] == 0 and item["outstanding_work"] == 0 for item in snapshot.values()
    )


@pytest.mark.asyncio
async def test_prefill_and_exactly_once_release() -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "x", max_work=10),))
    reservation = await tracker.reserve(8)
    await reservation.first_content(5)
    await reservation.first_content(5)
    assert (await tracker.snapshot())["r1"]["outstanding_work"] == 3
    assert await reservation.release() is True
    assert await reservation.release() is False
    state = (await tracker.snapshot())["r1"]
    assert state["outstanding_work"] == 0
    assert state["inflight"] == 0
    assert state["active_reservations"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("residue", [0.0, 5e-7, -5e-7])
async def test_drained_finite_work_within_tolerance_normalizes_to_zero(residue: float) -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "x", max_work=10),))
    reservation = await tracker.reserve(1.0)
    tracker.replicas["r1"].outstanding_work = 1.0 + residue
    assert await reservation.release() is True
    assert (await tracker.snapshot())["r1"] == {
        "lifecycle": "Healthy",
        "inflight": 0,
        "active_reservations": 0,
        "outstanding_work": 0.0,
        "passive_failures": 0,
    }


@pytest.mark.asyncio
async def test_unreleased_ledger_prevents_normalization() -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "x", max_work=10),))
    await tracker.reserve(1.0)
    tracker.replicas["r1"].outstanding_work = 5e-7
    state = (await tracker.snapshot())["r1"]
    assert state["inflight"] == state["active_reservations"] == 1
    assert state["outstanding_work"] == 5e-7


@pytest.mark.asyncio
async def test_inconsistent_reservation_identity_fails_release() -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "x", max_work=10),))
    reservation = await tracker.reserve(1.0)
    reservation.reservation_id = "not-the-active-identity"
    with pytest.raises(RuntimeError, match="identity is not active"):
        await reservation.release()


@pytest.mark.asyncio
async def test_current_generation_passive_failure_still_excludes_replica() -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "x"),))
    identity = await tracker.identity("r1")

    assert await tracker.record_passive_failure("r1", 1, expected=identity)
    state = tracker.replicas["r1"]
    assert state.lifecycle is ReplicaLifecycle.UNHEALTHY
    assert state.passive_failures == 1


@pytest.mark.asyncio
async def test_stale_generation_passive_failure_is_ignored() -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "x"),))
    stale_identity = await tracker.identity("r1")
    await tracker.new_generation("r1")

    assert not await tracker.record_passive_failure("r1", 1, expected=stale_identity)
    state = tracker.replicas["r1"]
    assert state.lifecycle is ReplicaLifecycle.HEALTHY
    assert state.passive_failures == 0
    assert (await tracker.lifecycle_snapshot())[-1]["event"] == (
        "stale_passive_failure_ignored"
    )
