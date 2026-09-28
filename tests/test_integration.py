from __future__ import annotations

import asyncio
import json

import pytest

from gateway.models import ReplicaLifecycle, RequestState
from gateway.prediction import ShadowPredictionConfig


def payload(**extra):
    value = {
        "model": "Qwen/Qwen3-4B-Instruct-2507",
        "prompt": "hello tokens",
        "max_tokens": 2,
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
        "ignore_eos": True,
        "seed": 42,
    }
    value.update(extra)
    return value


@pytest.mark.asyncio
async def test_sse_content_first_content_and_usage_preserved(integration_stack) -> None:
    client, gateway, _, _ = integration_stack
    response = await client.post(
        "/v1/completions", json=payload(), headers={"X-Request-Id": "preserve"}
    )
    body = await response.read()
    assert response.status == 200
    assert b'data: {"id":"fake","choices":[{"text":"hello"' in body
    assert b'"completion_tokens":2' in body
    assert body.endswith(b"data: [DONE]\n\n")
    item = gateway.records["preserve"]
    assert item.state is RequestState.COMPLETED
    assert item.final_usage == {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}
    assert item.timing.first_content is not None
    assert item.timing.measurements()["gateway_forwarding_delay"] >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "content_line_before_blank",
        "split_data_prefix",
        "split_json",
        "batched_events",
        "split_done",
    ],
)
async def test_sse_parsing_survives_adversarial_chunk_boundaries(
    integration_stack, mode: str
) -> None:
    client, gateway, fake1, fake2 = integration_stack
    response = await client.post(
        "/v1/completions",
        json=payload(test_mode=mode),
        headers={"X-Request-Id": f"fragment-{mode}"},
    )
    body = await response.read()

    record = gateway.records[f"fragment-{mode}"]
    assert response.status == 200
    assert body.endswith(b"data: [DONE]\n\n")
    assert record.state is RequestState.COMPLETED
    assert record.retry_count == 0
    assert record.final_usage == {
        "prompt_tokens": 2,
        "completion_tokens": 2,
        "total_tokens": 4,
    }
    assert fake1.requests == 1
    assert fake2.requests == 0


@pytest.mark.asyncio
async def test_fragmented_content_then_failure_never_retries(integration_stack) -> None:
    client, gateway, fake1, fake2 = integration_stack
    response = await client.post(
        "/v1/completions",
        json=payload(test_mode="fragmented_content_then_fail_replica1"),
        headers={"X-Request-Id": "fragmented-midstream"},
    )
    body = await response.read()

    record = gateway.records["fragmented-midstream"]
    assert b'"text":"hello"' in body
    assert b"backend_stream_failed" in body
    assert record.state is RequestState.FAILED
    assert record.retry_count == 0
    assert fake1.requests == 1
    assert fake2.requests == 0
    assert not any(event["event"] == "failover" for event in record.routing_events)


@pytest.mark.asyncio
async def test_timing_boundaries_exclude_queue(integration_stack) -> None:
    client, gateway, _, _ = integration_stack
    first = asyncio.create_task(
        client.post(
            "/v1/completions",
            json=payload(test_first_delay=0.08),
            headers={"X-Request-Id": "hold1"},
        )
    )
    second = asyncio.create_task(
        client.post(
            "/v1/completions",
            json=payload(test_first_delay=0.08),
            headers={"X-Request-Id": "hold2"},
        )
    )
    await asyncio.sleep(0.01)
    queued = await client.post(
        "/v1/completions", json=payload(), headers={"X-Request-Id": "queued"}
    )
    await queued.read()
    for response in await asyncio.gather(first, second):
        await response.read()
    metrics = gateway.records["queued"].timing.measurements()
    assert metrics["queue_residence"] > 0.03
    assert metrics["gateway_preprocessing"] < metrics["gateway_pre_dispatch"]
    assert metrics["backend_e2e"] < metrics["gateway_pre_dispatch"] + metrics["backend_e2e"]


@pytest.mark.asyncio
async def test_one_prestream_failover_excludes_failed_replica(integration_stack) -> None:
    client, gateway, fake1, fake2 = integration_stack
    response = await client.post(
        "/v1/completions",
        json=payload(test_mode="fail_replica1"),
        headers={"X-Request-Id": "retry"},
    )
    body = await response.read()
    assert response.status == 200
    assert b"[DONE]" in body
    item = gateway.records["retry"]
    assert item.retry_count == 1
    assert item.state is RequestState.COMPLETED
    assert fake1.requests == 1 and fake2.requests == 1
    assert (await gateway.tracker.snapshot())["replica1"]["passive_failures"] == 1


@pytest.mark.asyncio
async def test_failure_after_headers_before_content_fails_over(integration_stack) -> None:
    client, gateway, fake1, fake2 = integration_stack
    response = await client.post(
        "/v1/completions",
        json=payload(test_mode="fail_replica1_after_headers"),
        headers={"X-Request-Id": "after-headers"},
    )
    body = await response.read()
    item = gateway.records["after-headers"]
    assert item.state is RequestState.COMPLETED
    assert item.retry_count == 1
    assert fake1.requests == 1 and fake2.requests == 1
    assert b"[DONE]" in body


@pytest.mark.asyncio
async def test_stale_dispatch_failure_cannot_poison_new_generation(integration_stack) -> None:
    client, gateway, fake1, fake2 = integration_stack

    request_task = asyncio.create_task(
        client.post(
            "/v1/completions",
            json=payload(
                test_mode="delayed_fail_replica1_after_headers",
                test_failure_delay=0.05,
            ),
            headers={"X-Request-Id": "stale-passive-failure"},
        )
    )
    await asyncio.wait_for(fake1.request_started.wait(), 0.2)
    await gateway.tracker.new_generation("replica1")

    response = await request_task
    body = await response.read()
    record = gateway.records["stale-passive-failure"]
    replica = gateway.tracker.replicas["replica1"]

    assert b"[DONE]" in body
    assert record.state is RequestState.COMPLETED
    assert record.retry_count == 1
    assert fake1.requests == 1 and fake2.requests == 1
    assert replica.lifecycle is ReplicaLifecycle.HEALTHY
    assert replica.passive_failures == 0
    assert any(
        event["event"] == "stale_passive_failure_ignored"
        for event in await gateway.tracker.lifecycle_snapshot()
    )


@pytest.mark.asyncio
async def test_no_retry_after_streaming_begins(integration_stack) -> None:
    client, gateway, fake1, fake2 = integration_stack
    response = await client.post(
        "/v1/completions",
        json=payload(test_mode="midstream"),
        headers={"X-Request-Id": "midstream"},
    )
    body = await response.read()
    item = gateway.records["midstream"]
    assert item.state is RequestState.FAILED
    assert item.retry_count == 0
    assert fake1.requests + fake2.requests == 1
    assert b"backend_stream_failed" in body


@pytest.mark.asyncio
async def test_replica_unavailability_returns_503(integration_stack) -> None:
    client, gateway, _, _ = integration_stack
    await gateway.tracker.set_lifecycle("replica1", ReplicaLifecycle.UNHEALTHY)
    await gateway.tracker.set_lifecycle("replica2", ReplicaLifecycle.HALF_OPEN)
    response = await client.post(
        "/v1/completions", json=payload(), headers={"X-Request-Id": "none"}
    )
    assert response.status == 503
    assert (await response.json())["error"]["code"] == "no_healthy_replica"


@pytest.mark.asyncio
async def test_client_cancellation_releases_reservation(integration_stack) -> None:
    client, gateway, _, _ = integration_stack
    response = await client.post(
        "/v1/completions",
        json=payload(test_first_delay=0.5),
        headers={"X-Request-Id": "cancel"},
    )
    await asyncio.sleep(0.03)
    response.close()
    await asyncio.sleep(0.15)
    state = await gateway.reconcile()
    assert gateway.records["cancel"].state is RequestState.CANCELLED
    assert all(item["inflight"] == 0 for item in state["replicas"].values())
    assert all(item["outstanding_work"] == 0 for item in state["replicas"].values())


@pytest.mark.asyncio
async def test_concurrent_requests_leave_no_accounting_leaks(integration_stack) -> None:
    client, gateway, _, _ = integration_stack

    async def one(index: int):
        response = await client.post(
            "/v1/completions",
            json=payload(test_event_delay=0.005),
            headers={"X-Request-Id": f"concurrent-{index}"},
        )
        return response.status, await response.read()

    results = await asyncio.gather(*(one(index) for index in range(6)))
    assert all(status == 200 and b"[DONE]" in body for status, body in results)
    state = await gateway.reconcile()
    assert state["queue"]["queued_requests"] == 0
    assert state["queue"]["queued_work"] == 0.0
    assert all(item["inflight"] == 0 for item in state["replicas"].values())
    assert all(item["outstanding_work"] == 0 for item in state["replicas"].values())


@pytest.mark.asyncio
async def test_shadow_scorer_failure_cannot_fail_dispatch_or_leak_reservation(
    integration_stack,
) -> None:
    client, gateway, _, _ = integration_stack

    class ExplodingPredictor:
        config = ShadowPredictionConfig("exploding")

        def score(self, _reservation):
            raise RuntimeError("scorer defect")

    gateway.shadow_predictor = ExplodingPredictor()
    response = await client.post(
        "/v1/completions", json=payload(), headers={"X-Request-Id": "scorer-failure"}
    )
    body = await response.read()

    assert response.status == 200
    assert b"[DONE]" in body
    record = gateway.records["scorer-failure"]
    assert record.state is RequestState.COMPLETED
    prediction = next(
        item for item in record.routing_events if item["event"] == "shadow_prediction"
    )
    assert prediction["prediction_error"] == "RuntimeError"
    assert all(value is None for value in prediction["predictions"].values())
    prediction_reasons = prediction["prediction_reasons"].values()
    assert all(value == "scorer_error:RuntimeError" for value in prediction_reasons)
    accounting = await gateway.reconcile()
    for item in accounting["replicas"].values():
        assert item["inflight"] == 0
        assert item["outstanding_work"] == 0
        assert item["active_reservations"] == 0


@pytest.mark.asyncio
async def test_parameters_are_forwarded_unchanged(integration_stack) -> None:
    client, gateway, _, _ = integration_stack
    request_body = payload(custom_extension={"value": 7})
    response = await client.post(
        "/v1/completions", json=request_body, headers={"X-Request-Id": "params"}
    )
    await response.read()
    assert gateway.records["params"].body == request_body


@pytest.mark.asyncio
async def test_accounting_endpoint_reports_zero(integration_stack) -> None:
    client, _, _, _ = integration_stack
    response = await client.get("/_stage_a/accounting")
    data = await response.json()
    assert data["queue"]["queued_requests"] == 0
    assert data["queue"]["queued_work"] == 0.0
    assert all(item["inflight"] == 0 for item in data["replicas"].values())
    assert all(item["outstanding_work"] == 0 for item in data["replicas"].values())


@pytest.mark.asyncio
async def test_empty_comment_and_heartbeat_events_do_not_release_prefill(integration_stack) -> None:
    client, gateway, _, _ = integration_stack
    task = asyncio.create_task(
        client.post(
            "/v1/completions",
            json=payload(test_mode="heartbeat_before_content", test_event_delay=0.12),
            headers={"X-Request-Id": "heartbeat"},
        )
    )
    await asyncio.sleep(0.05)
    events = gateway.records["heartbeat"].routing_events
    assert [item["event"] for item in events] == ["reservation", "backend_dispatch"]
    response = await task
    await response.read()
    assert [item["event"] for item in events] == [
        "reservation",
        "backend_dispatch",
        "prefill_release",
        "final_release",
        "final_accounting",
    ]


def test_usage_event_is_valid_json() -> None:
    event = {"usage": {"prompt_tokens": 2, "completion_tokens": 2}}
    assert json.loads(json.dumps(event)) == event
