from __future__ import annotations

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway.app import Gateway, create_app
from gateway.config import GatewayConfig, ReplicaConfig
from gateway.models import RequestState
from gateway.prediction import CountAdmissionPolicy
from gateway.tracker import PredictionAdmissionRejection, ReplicaTracker, Reservation
from tests.conftest import FixedCounter
from tests.fake_backend import FakeBackend
from tests.test_integration import payload


@pytest.mark.asyncio
async def test_count_seven_admits_and_count_eight_rejects() -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "unused", max_inflight=20),))
    policy = CountAdmissionPolicy(8)
    reservations: list[Reservation] = []
    for _ in range(8):
        result = await tracker.reserve(1, prediction_decider=policy.decide, enforce_prediction=True)
        assert isinstance(result, Reservation)
        reservations.append(result)
    events: list[dict[str, object]] = []
    rejected = await tracker.reserve(
        1,
        events=events,
        prediction_decider=policy.decide,
        enforce_prediction=True,
    )
    assert isinstance(rejected, PredictionAdmissionRejection)
    assert rejected.observed_count == rejected.threshold == 8
    assert events[-1]["admission_outcome"] == "rejected"
    state = (await tracker.snapshot())["r1"]
    assert state["inflight"] == state["active_reservations"] == 8
    assert state["outstanding_work"] == 8
    for reservation in reservations:
        await reservation.release()


@pytest.mark.asyncio
async def test_concurrent_prediction_admission_is_atomic() -> None:
    tracker = ReplicaTracker((ReplicaConfig("r1", "unused", max_inflight=20),))
    policy = CountAdmissionPolicy(1)
    results = await asyncio.gather(
        *(
            tracker.reserve(1, prediction_decider=policy.decide, enforce_prediction=True)
            for _ in range(2)
        )
    )
    assert sum(isinstance(result, Reservation) for result in results) == 1
    assert sum(isinstance(result, PredictionAdmissionRejection) for result in results) == 1
    reservation = next(result for result in results if isinstance(result, Reservation))
    await reservation.release()


@pytest.mark.asyncio
async def test_rejection_consumes_round_robin_turn_without_rerouting() -> None:
    tracker = ReplicaTracker(
        (ReplicaConfig("r1", "unused", max_inflight=20), ReplicaConfig("r2", "unused", 20))
    )
    policy = CountAdmissionPolicy(1)
    first = await tracker.reserve(1, prediction_decider=policy.decide, enforce_prediction=True)
    second = await tracker.reserve(1, prediction_decider=policy.decide, enforce_prediction=True)
    rejected = await tracker.reserve(1, prediction_decider=policy.decide, enforce_prediction=True)
    assert isinstance(first, Reservation) and first.replica_id == "r1"
    assert isinstance(second, Reservation) and second.replica_id == "r2"
    assert isinstance(rejected, PredictionAdmissionRejection) and rejected.replica_id == "r1"
    await second.release()
    fourth = await tracker.reserve(1, prediction_decider=policy.decide, enforce_prediction=True)
    assert isinstance(fourth, Reservation) and fourth.replica_id == "r2"
    await first.release()
    await fourth.release()


@pytest.fixture
async def e8_stack():
    fake1, fake2 = FakeBackend("replica1"), FakeBackend("replica2")
    server1, server2 = TestServer(fake1.app()), TestServer(fake2.app())
    await server1.start_server()
    await server2.start_server()
    config = GatewayConfig(
        replicas=(
            ReplicaConfig("replica1", str(server1.make_url("")).rstrip("/"), max_inflight=16),
            ReplicaConfig("replica2", str(server2.make_url("")).rstrip("/"), max_inflight=16),
        ),
        max_queued_requests=0,
        prediction_admission_mode="enforce",
        prediction_admission_count_threshold=1,
    )
    gateway = Gateway(
        config,
        token_counter=FixedCounter(),
        prediction_admission_policy=CountAdmissionPolicy(1),
    )
    client = TestClient(TestServer(create_app(gateway)))
    await client.start_server()
    try:
        yield client, gateway, fake1, fake2
    finally:
        await client.close()
        await server1.close()
        await server2.close()


@pytest.mark.asyncio
async def test_rejection_never_dispatches_or_leaks(e8_stack) -> None:
    client, gateway, fake1, fake2 = e8_stack
    first = asyncio.create_task(
        client.post(
            "/v1/completions",
            json=payload(test_first_delay=0.15),
            headers={"X-Request-Id": "hold-r1"},
        )
    )
    second = asyncio.create_task(
        client.post(
            "/v1/completions",
            json=payload(test_first_delay=0.15),
            headers={"X-Request-Id": "hold-r2"},
        )
    )
    for _ in range(100):
        if fake1.requests + fake2.requests == 2:
            break
        await asyncio.sleep(0.005)
    assert fake1.requests + fake2.requests == 2
    response = await client.post(
        "/v1/completions", json=payload(), headers={"X-Request-Id": "reject"}
    )
    assert response.status == 429
    assert (await response.json())["error"]["code"] == "prediction_count_threshold"
    assert fake1.requests + fake2.requests == 2
    record = gateway.records["reject"]
    assert record.state is RequestState.REJECTED_429
    assert not any(event["event"] == "reservation" for event in record.routing_events)
    assert not any(event["event"] == "backend_dispatch" for event in record.routing_events)
    for accepted in await asyncio.gather(first, second):
        await accepted.read()
    accounting = await gateway.reconcile()
    for replica in accounting["replicas"].values():
        assert replica["inflight"] == 0
        assert replica["active_reservations"] == 0
        assert replica["outstanding_work"] == 0


@pytest.mark.asyncio
async def test_shadow_mode_is_permissive_and_decision_precedes_dispatch(e8_stack) -> None:
    client, gateway, fake1, fake2 = e8_stack
    gateway.admission.enforce_prediction = False
    response = await client.post(
        "/v1/completions", json=payload(), headers={"X-Request-Id": "baseline"}
    )
    await response.read()
    assert response.status == 200
    assert fake1.requests + fake2.requests == 1
    events = gateway.records["baseline"].routing_events
    decision_index = next(
        index
        for index, event in enumerate(events)
        if event["event"] == "prediction_admission_decision"
    )
    dispatch_index = next(
        index for index, event in enumerate(events) if event["event"] == "backend_dispatch"
    )
    assert decision_index < dispatch_index
    assert events[decision_index]["enforcement_enabled"] is False


@pytest.mark.asyncio
async def test_scorer_error_fails_open_with_explicit_evidence(e8_stack) -> None:
    client, gateway, fake1, fake2 = e8_stack

    def explode(_features):
        raise RuntimeError("injected")

    gateway.admission.prediction_decider = explode
    response = await client.post(
        "/v1/completions", json=payload(), headers={"X-Request-Id": "fail-open"}
    )
    await response.read()
    assert response.status == 200
    assert fake1.requests + fake2.requests == 1
    decision = gateway.records["fail-open"].routing_events[0]
    assert decision["admission_outcome"] == "fail_open"
    assert decision["decision_reason"] == "scorer_error:RuntimeError"


@pytest.mark.asyncio
async def test_malformed_reject_decision_fails_open(e8_stack) -> None:
    client, gateway, fake1, fake2 = e8_stack
    gateway.admission.prediction_decider = lambda _features: {"predicted_reject": True}
    response = await client.post(
        "/v1/completions", json=payload(), headers={"X-Request-Id": "malformed"}
    )
    await response.read()
    assert response.status == 200
    assert fake1.requests + fake2.requests == 1
    decision = gateway.records["malformed"].routing_events[0]
    assert decision["admission_outcome"] == "fail_open"
    assert decision["decision_reason"] == "scorer_error:TypeError"
