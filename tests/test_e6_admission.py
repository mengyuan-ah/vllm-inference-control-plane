from __future__ import annotations

import asyncio

import pytest

from gateway.admission import AdmissionController, AdmissionError
from gateway.config import ReplicaConfig
from gateway.models import RequestEnvelope, RequestState
from gateway.tracker import ReplicaTracker, Reservation


def envelope() -> RequestEnvelope:
    item = RequestEnvelope({"stream": True}, 1024, 128, 14_759, 1024)
    item.transition(RequestState.VALIDATING)
    item.transition(RequestState.ADMISSION_CHECK)
    return item


class PausingTracker(ReplicaTracker):
    def __init__(self) -> None:
        super().__init__((ReplicaConfig("r1", "x", max_inflight=1, max_work=1e9),))
        self.pause_reserve = False
        self.reserve_started = asyncio.Event()
        self.allow_reserve = asyncio.Event()

    async def reserve(self, *args, **kwargs):
        if self.pause_reserve:
            self.reserve_started.set()
            await self.allow_reserve.wait()
        return await super().reserve(*args, **kwargs)


async def queued_race() -> tuple[
    PausingTracker, AdmissionController, RequestEnvelope, asyncio.Task[Reservation]
]:
    tracker = PausingTracker()
    admission = AdmissionController(tracker, max_requests=1, max_work=1e10, max_residence=0.02)
    hold = await admission.admit(envelope())
    queued = envelope()
    task = asyncio.create_task(admission.admit(queued))
    await asyncio.sleep(0)
    assert (await admission.snapshot())["queued_requests"] == 1
    tracker.pause_reserve = True
    await hold.release()
    await asyncio.wait_for(tracker.reserve_started.wait(), 0.1)
    return tracker, admission, queued, task


@pytest.mark.asyncio
async def test_b_immediately_rejects_when_both_replicas_are_full() -> None:
    tracker = ReplicaTracker(
        (
            ReplicaConfig("r1", "x", max_inflight=1, max_work=1e9),
            ReplicaConfig("r2", "y", max_inflight=1, max_work=1e9),
        )
    )
    admission = AdmissionController(tracker, max_requests=0, max_work=1e10, max_residence=1)
    reservations = [await admission.admit(envelope()), await admission.admit(envelope())]
    with pytest.raises(AdmissionError) as error:
        await admission.admit(envelope())
    assert (error.value.status, error.value.reason) == (429, "queue_request_limit")
    for reservation in reservations:
        assert await reservation.release()
        assert not await reservation.release()
    state = await tracker.snapshot()
    assert all(row["active_reservations"] == 0 for row in state.values())


@pytest.mark.asyncio
async def test_c_fifo_dispatch_timeout_and_cancel_leave_empty_accounting() -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "x", max_inflight=1, max_work=1e9),))
    admission = AdmissionController(tracker, max_requests=3, max_work=1e10, max_residence=0.03)
    first = await admission.admit(envelope())
    queued_one = envelope()
    queued_two = envelope()
    one = asyncio.create_task(admission.admit(queued_one))
    two = asyncio.create_task(admission.admit(queued_two))
    await asyncio.sleep(0)
    two.cancel()
    with pytest.raises(asyncio.CancelledError):
        await two
    await first.release()
    dispatched = await one
    assert queued_one.routing_events[-1]["event"] == "queue_dispatch"
    await dispatched.release()
    hold = await admission.admit(envelope())
    with pytest.raises(AdmissionError, match="queue_timeout"):
        await admission.admit(envelope())
    await hold.release()
    snapshot = await admission.snapshot()
    assert snapshot["queued_requests"] == 0 and snapshot["queued_work"] == 0


@pytest.mark.asyncio
async def test_queue_timeout_racing_dispatch_releases_late_reservation() -> None:
    tracker, admission, queued, task = await queued_race()
    await asyncio.sleep(0.03)
    tracker.allow_reserve.set()

    with pytest.raises(AdmissionError, match="queue_timeout"):
        await task

    assert queued.state is RequestState.REJECTED_429
    events = [item["event"] for item in queued.routing_events]
    assert events.count("reservation") == 1
    assert events.count("final_release") == 1
    assert events.count("queue_dispatch") == 0
    snapshot = await admission.snapshot()
    state = await tracker.snapshot()
    assert snapshot["queued_requests"] == 0 and snapshot["queued_work"] == 0
    assert state["r1"]["active_reservations"] == 0
    assert state["r1"]["outstanding_work"] == 0.0


@pytest.mark.asyncio
async def test_queue_cancellation_racing_dispatch_releases_reservation() -> None:
    tracker, admission, queued, task = await queued_race()
    task.cancel()
    await asyncio.sleep(0)
    tracker.allow_reserve.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert queued.state is RequestState.CANCELLED
    events = [item["event"] for item in queued.routing_events]
    assert events.count("reservation") == 1
    assert events.count("final_release") == 1
    snapshot = await admission.snapshot()
    state = await tracker.snapshot()
    assert snapshot["queued_requests"] == 0 and snapshot["queued_work"] == 0
    assert state["r1"]["active_reservations"] == 0
    assert state["r1"]["outstanding_work"] == 0.0
