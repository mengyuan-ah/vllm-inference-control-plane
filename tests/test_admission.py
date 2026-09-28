from __future__ import annotations

import asyncio

import pytest

from gateway.admission import AdmissionController, AdmissionError
from gateway.config import ReplicaConfig
from gateway.models import ReplicaLifecycle, RequestEnvelope, RequestState
from gateway.tracker import ReplicaTracker


def env(work: float = 1) -> RequestEnvelope:
    item = RequestEnvelope({"stream": True}, 1, 1, work, 0)
    item.transition(RequestState.VALIDATING)
    item.transition(RequestState.ADMISSION_CHECK)
    return item


def controller(*, queue_count=1, queue_work=10, timeout=0.03):
    tracker = ReplicaTracker((ReplicaConfig("r1", "x", max_inflight=1, max_work=10),))
    admission = AdmissionController(
        tracker,
        max_requests=queue_count,
        max_work=queue_work,
        max_residence=timeout,
    )
    return tracker, admission


@pytest.mark.asyncio
async def test_queue_fifo_dispatch_and_accounting() -> None:
    tracker, admission = controller(queue_count=2)
    first = await admission.admit(env())
    second_env = env()
    third_env = env()
    second_task = asyncio.create_task(admission.admit(second_env))
    third_task = asyncio.create_task(admission.admit(third_env))
    await asyncio.sleep(0)
    assert (await admission.snapshot()) == {
        "queued_requests": 2,
        "queued_work": 2.0,
        "peak_queued_requests": 2,
        "peak_queued_work": 2.0,
    }
    await first.release()
    second = await second_task
    assert second_env.state is RequestState.RESERVED
    assert not third_task.done()
    await second.release()
    third = await third_task
    await third.release()
    snapshot = await admission.snapshot()
    assert snapshot["queued_requests"] == 0
    assert snapshot["queued_work"] == 0.0
    assert snapshot["peak_queued_requests"] == 2
    assert "queue_dispatch" in [event["event"] for event in second_env.routing_events]


@pytest.mark.asyncio
async def test_enqueue_rechecks_capacity_after_missed_release_notification() -> None:
    tracker, admission = controller(timeout=1)
    first = await admission.admit(env())
    queued = env()
    immediate_reserve_failed = asyncio.Event()
    allow_enqueue = asyncio.Event()
    original_reserve = admission._reserve

    async def reserve_with_release_window(envelope, *, exclude=None):
        result = await original_reserve(envelope, exclude=exclude)
        if envelope is queued and not immediate_reserve_failed.is_set():
            assert result is None
            immediate_reserve_failed.set()
            await allow_enqueue.wait()
        return result

    admission._reserve = reserve_with_release_window
    task = asyncio.create_task(admission.admit(queued))
    await immediate_reserve_failed.wait()

    await first.release()
    # Reproduce the old lost wakeup deterministically: the release-triggered drain
    # observes an empty queue and exits before this request can append itself.
    await admission.drain()
    allow_enqueue.set()

    reservation = await asyncio.wait_for(task, 0.1)
    assert reservation.replica_id == "r1"
    assert queued.state is RequestState.RESERVED
    assert (await admission.snapshot())["queued_requests"] == 0
    await reservation.release()


@pytest.mark.asyncio
async def test_queue_count_limit_returns_429() -> None:
    _, admission = controller(queue_count=0)
    reservation = await admission.admit(env())
    with pytest.raises(AdmissionError) as error:
        await admission.admit(env())
    assert (error.value.status, error.value.reason) == (429, "queue_request_limit")
    await reservation.release()


@pytest.mark.asyncio
async def test_queue_work_limit_returns_429() -> None:
    _, admission = controller(queue_work=1)
    reservation = await admission.admit(env())
    with pytest.raises(AdmissionError) as error:
        await admission.admit(env(2))
    assert error.value.reason == "queue_work_limit"
    await reservation.release()


@pytest.mark.asyncio
async def test_queue_timeout_releases_accounting() -> None:
    _, admission = controller(timeout=0.01)
    reservation = await admission.admit(env())
    queued = env()
    with pytest.raises(AdmissionError) as error:
        await admission.admit(queued)
    assert error.value.reason == "queue_timeout"
    assert queued.state is RequestState.REJECTED_429
    snapshot = await admission.snapshot()
    assert snapshot["queued_requests"] == 0
    assert snapshot["queued_work"] == 0.0
    await reservation.release()


@pytest.mark.asyncio
async def test_503_when_no_serving_replica() -> None:
    tracker, admission = controller()
    await tracker.set_lifecycle("r1", ReplicaLifecycle.UNHEALTHY)
    with pytest.raises(AdmissionError) as error:
        await admission.admit(env())
    assert (error.value.status, error.value.reason) == (503, "no_healthy_replica")


@pytest.mark.asyncio
async def test_queued_request_losing_all_replicas_gets_503() -> None:
    tracker, admission = controller(timeout=1)
    reservation = await admission.admit(env())
    queued = env()
    task = asyncio.create_task(admission.admit(queued))
    await asyncio.sleep(0)
    await tracker.set_lifecycle("r1", ReplicaLifecycle.UNHEALTHY)
    with pytest.raises(AdmissionError) as error:
        await task
    assert (error.value.status, error.value.reason) == (503, "replicas_unavailable")
    assert queued.state is RequestState.REJECTED_503
    await reservation.release()


@pytest.mark.asyncio
async def test_queue_cancellation_releases_accounting() -> None:
    _, admission = controller(timeout=1)
    reservation = await admission.admit(env())
    queued = env()
    task = asyncio.create_task(admission.admit(queued))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert queued.state is RequestState.CANCELLED
    snapshot = await admission.snapshot()
    assert snapshot["queued_requests"] == 0
    assert snapshot["queued_work"] == 0.0
    await reservation.release()
