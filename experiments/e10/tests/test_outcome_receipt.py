import asyncio
import json

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from gateway.app import Gateway, create_app
from gateway.config import GatewayConfig, ReplicaConfig
from gateway.lifecycle import ReplicaLifecycleManager
from gateway.models import ReplicaLifecycle

from analyze import analyze
from e10.runtime import FROZEN_BETA, StreamObservation, classify_outcome
from run_harness import offer


class Counter:
    def count(self, prompt):
        return len(prompt)


class Fake:
    def __init__(self, mode="complete"):
        self.mode = mode
        self.calls = 0

    def app(self):
        app = web.Application()
        app.router.add_post("/v1/completions", self.handle)
        return app

    async def handle(self, request):
        self.calls += 1
        body = await request.json()
        if self.mode == "before":
            return web.Response(status=503)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b'data: {"choices":[{"text":"x"}]}\n\n')
        if self.mode == "disconnect":
            request.transport.close()
            return response
        if self.mode == "timeout":
            await asyncio.sleep(0.15)
            return response
        if self.mode == "error":
            await response.write(b'data: {"error":{"message":"interrupted"}}\n\n')
            return response
        if self.mode == "error_complete":
            await response.write(b'data: {"error":{"message":"interrupted"}}\n\n')
        final = {
            "choices": [{"text": "", "finish_reason": "length"}],
            "usage": {"prompt_tokens": 1024, "completion_tokens": body["max_tokens"]},
        }
        await response.write(f"data: {json.dumps(final)}\n\ndata: [DONE]\n\n".encode())
        return response


@pytest.mark.parametrize(
    "mode,alternate,expected",
    [
        ("before", "before", "failed_before_content"),
        ("disconnect", "complete", "failed_or_truncated_after_content"),
        ("timeout", "complete", "failed_or_truncated_after_content"),
        ("error", "complete", "failed_or_truncated_after_content"),
        ("complete", "complete", "success_no_retry"),
        ("before", "complete", "success_after_precontent_failover"),
        ("rejected", "complete", "rejected_no_serving_replica"),
    ],
)
async def test_real_offer_through_gateway(mode, alternate, expected):
    backends = [Fake(mode), Fake(alternate)]
    servers = [TestServer(item.app()) for item in backends]
    for server in servers:
        await server.start_server()
    gateway = Gateway(
        GatewayConfig(
            replicas=tuple(
                ReplicaConfig(f"r{i}", str(s.make_url("")).rstrip("/"))
                for i, s in enumerate(servers)
            ),
            beta=FROZEN_BETA,
            backend_timeout=0.08,
            max_queued_requests=0,
        ),
        token_counter=Counter(),
    )
    front = TestServer(create_app(gateway))
    await front.start_server()
    row = {"request_id": "receipt", "prompt": "x" * 1024}
    try:
        if mode == "rejected":
            for name in gateway.tracker.order:
                await gateway.tracker.set_lifecycle(name, ReplicaLifecycle.UNHEALTHY, "test")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
            await offer(session, str(front.make_url("")).rstrip("/"), row)
        record = gateway.records["receipt"]
        row.update(gateway_state=record.state.value, retry_count=record.retry_count)
        assert classify_outcome(row) == expected
        assert record.retry_count <= 1
        if mode in {"disconnect", "timeout", "error"}:
            assert row["content_received"] and row["ttft_s"] is not None
            assert backends[1].calls == 0 and record.retry_count == 0
        if not expected.startswith("success"):
            assert not row["success"] and not row["slo_success"]
        accounting = await gateway.reconcile()
        for replica in accounting["replicas"].values():
            assert replica["inflight"] == replica["outstanding_work"] == 0
            assert not replica["active_reservations"]
    finally:
        await front.close()
        for server in servers:
            await server.close()


async def test_client_timeout_preserves_content_and_cannot_be_success():
    server = TestServer(Fake("timeout").app())
    await server.start_server()
    row = {"request_id": "timeout", "prompt": "x" * 1024}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=0.05)) as session:
            await offer(session, str(server.make_url("")).rstrip("/"), row)
        assert row["timed_out"] and row["transport_error"]
        assert classify_outcome(row) == "failed_or_truncated_after_content"
        assert not row["success"] and not row["slo_success"]
    finally:
        await server.close()


def test_gateway_completed_does_not_override_incomplete_client_receipt():
    observation = StreamObservation()
    observation.feed('{"choices":[{"text":"x"}]}', 1.1)
    row = observation.metrics(1, 1.2)
    row["gateway_state"] = "Completed"
    assert classify_outcome(row) == "failed_or_truncated_after_content"
    assert not row["success"] and not row["slo_success"]


@pytest.mark.parametrize(
    "update",
    [
        {},
        {"content_received": False, "ttft_s": 0.1},
        {"content_received": True, "ttft_s": 0.1, "gateway_state": "Rejected503"},
    ],
)
def test_missing_or_contradictory_evidence_is_visible(update):
    assert classify_outcome(update) == "invalid_evidence"
    assert update["outcome_evidence_errors"]


def test_transport_error_after_done_is_not_success():
    observation = StreamObservation()
    observation.feed(
        '{"choices":[{"text":"x","finish_reason":"length"}],"usage":{"completion_tokens":128}}', 1.1
    )
    observation.feed("[DONE]", 1.2)
    observation.transport_error = True
    row = observation.metrics(1, 1.3)
    assert not row["success"] and not row["slo_success"]
    assert classify_outcome(row) == "failed_or_truncated_after_content"


@pytest.mark.parametrize("failure", ["before", "disconnect", "error_complete"])
async def test_realistic_recovery_probe_four_successes_regression_and_cleanup(failure):
    backend = Fake()
    server = TestServer(backend.app())
    await server.start_server()
    config = GatewayConfig(
        replicas=(ReplicaConfig("r0", str(server.make_url("")).rstrip("/")),),
        beta=FROZEN_BETA,
        max_queued_requests=0,
    )
    gateway = Gateway(config, token_counter=Counter())
    front = TestServer(create_app(gateway))
    await front.start_server()
    manager = ReplicaLifecycleManager(config, gateway.tracker)
    manager.session = aiohttp.ClientSession()
    try:
        await gateway.tracker.record_passive_failure("r0", 1)
        assert gateway.tracker.replicas["r0"].lifecycle is ReplicaLifecycle.UNHEALTHY
        assert await gateway.tracker.reserve(14756.0981376) is None
        await gateway.tracker.set_lifecycle("r0", ReplicaLifecycle.HALF_OPEN, "health_success")
        assert await gateway.tracker.reserve(14756.0981376) is None
        manager._launch_probe("r0")
        task = manager.probe_tasks["r0"]
        manager._launch_probe("r0")
        assert manager.probe_tasks["r0"] is task
        await task
        assert backend.calls == 1
        state = gateway.tracker.replicas["r0"]
        assert state.lifecycle is ReplicaLifecycle.RECOVERING
        assert not state.half_open_probe_active
        reservation = await gateway.tracker.reserve(14756.0981376)
        assert reservation is not None
        assert await gateway.tracker.reserve(14756.0981376) is None
        await reservation.release()
        async with aiohttp.ClientSession() as session:
            for index in range(4):
                row = {"request_id": f"recovery-{index}", "prompt": "x" * 1024}
                await offer(session, str(front.make_url("")).rstrip("/"), row)
                assert row["success"]
                expected = ReplicaLifecycle.HEALTHY if index == 3 else ReplicaLifecycle.RECOVERING
                assert state.lifecycle is expected
            # Normal routing after promotion is no longer restricted to one reservation.
            first = await gateway.tracker.reserve(14756.0981376)
            second = await gateway.tracker.reserve(14756.0981376)
            assert first is not None and second is not None
            await first.release()
            await second.release()
            await gateway.tracker.set_lifecycle(
                "r0", ReplicaLifecycle.RECOVERING, "test_regression"
            )
            backend.mode = failure
            row = {"request_id": "regression", "prompt": "x" * 1024}
            await offer(session, str(front.make_url("")).rstrip("/"), row)
            assert not row["success"]
            assert state.lifecycle is ReplicaLifecycle.UNHEALTHY
            assert await gateway.tracker.reserve(14756.0981376) is None
        accounting = await gateway.reconcile()
        for replica in accounting["replicas"].values():
            assert replica["inflight"] == replica["outstanding_work"] == 0
            assert not replica["active_reservations"]
    finally:
        await manager.close()
        await front.close()
        await server.close()
    assert not manager.tasks and not manager.probe_tasks and manager.session is None


@pytest.mark.parametrize(
    "usage",
    [
        None,
        [],
        {"completion_tokens": "128"},
        {"completion_tokens": 128.5},
        {"completion_tokens": 127},
    ],
)
def test_invalid_usage_never_counts_as_success(usage):
    observation = StreamObservation(
        first_content_monotonic=1.1, usage=usage, finish_reason="length", saw_done=True
    )
    row = observation.metrics(1, 1.2)
    assert not row["success"] and not row["slo_success"]


async def test_cancelled_offer_retains_client_observation():
    server = TestServer(Fake("timeout").app())
    await server.start_server()
    row = {"request_id": "cancel", "prompt": "x" * 1024}
    try:
        async with aiohttp.ClientSession() as session:
            task = asyncio.create_task(offer(session, str(server.make_url("")).rstrip("/"), row))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert row["client_cancelled"] and row["transport_error"]
        assert row["content_received"] and not row["slo_success"]
        assert classify_outcome(row) == "failed_or_truncated_after_content"
    finally:
        await server.close()


def test_v2_analyzer_rejects_missing_content_evidence(tmp_path):
    arm = tmp_path / "arm"
    arm.mkdir()
    (arm / "manifest.json").write_text(
        json.dumps(
            {
                "arm_id": "arm",
                "pair_id": "pair",
                "condition": "control",
                "offered": 1,
                "outcome_schema_version": 2,
            }
        )
    )
    (arm / "requests.jsonl").write_text(
        json.dumps(
            {
                "request_id": "one",
                "outcome": "failed_before_content",
                "success": False,
                "input_tokens": 1024,
                "max_tokens": 128,
            }
        )
        + "\n"
    )
    result = analyze(tmp_path)
    assert not result["valid"]
    assert "arm: inconsistent client outcome evidence" in result["failures"]
