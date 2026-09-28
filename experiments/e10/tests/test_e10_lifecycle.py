import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from gateway.config import GatewayConfig, ReplicaConfig
from gateway.lifecycle import ReplicaLifecycleManager
from gateway.models import ReplicaLifecycle
from gateway.tracker import ReplicaTracker


class Backend:
    def __init__(self):
        self.health = False
        self.inference = False
        self.active = 0
        self.peak = 0

    def app(self):
        app = web.Application()
        app.router.add_get("/health", self.health_handler)
        app.router.add_post("/v1/completions", self.complete)
        return app

    async def health_handler(self, _request):
        return web.Response(status=200 if self.health else 503)

    async def complete(self, request):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if not self.inference:
                return web.Response(status=503)
            body = await request.json()
            await asyncio.sleep(0.03)
            usage = {
                "prompt_tokens": 2,
                "completion_tokens": body["max_tokens"],
                "total_tokens": 2 + body["max_tokens"],
            }
            return web.Response(
                text=f"data: {json.dumps({'usage': usage})}\n\ndata: [DONE]\n\n",
                content_type="text/event-stream",
            )
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_health_does_not_restore_without_one_inference_probe_and_shutdown_is_clean():
    backend = Backend()
    server = TestServer(backend.app())
    await server.start_server()
    config = GatewayConfig(
        replicas=(ReplicaConfig("replica1", str(server.make_url("")).rstrip("/")),),
        lifecycle_enabled=True,
        health_probe_interval=0.01,
        health_probe_timeout=0.05,
        health_failure_threshold=1,
        health_success_threshold=1,
        inference_probe_timeout=0.2,
    )
    tracker = ReplicaTracker(config.replicas)
    manager = ReplicaLifecycleManager(config, tracker)
    await manager.start()
    try:
        await asyncio.sleep(0.04)
        assert tracker.replicas["replica1"].lifecycle is ReplicaLifecycle.UNHEALTHY
        backend.health = True
        await asyncio.sleep(0.08)
        assert tracker.replicas["replica1"].lifecycle in {
            ReplicaLifecycle.HALF_OPEN,
            ReplicaLifecycle.UNHEALTHY,
        }
        assert backend.peak <= 1
        backend.inference = True
        await asyncio.sleep(0.12)
        assert tracker.replicas["replica1"].lifecycle is ReplicaLifecycle.RECOVERING
    finally:
        await manager.close()
        await server.close()
    assert not manager.tasks and not manager.probe_tasks


@pytest.mark.asyncio
async def test_passive_detection_recovery_headroom_and_regression():
    config = GatewayConfig(
        replicas=(
            ReplicaConfig(
                "replica1", "http://127.0.0.1:1", max_inflight=8, recovery_max_inflight=1
            ),
        ),
        passive_failure_threshold=2,
        recovery_success_threshold=2,
    )
    tracker = ReplicaTracker(config.replicas)
    assert not await tracker.record_passive_failure("replica1", 2)
    assert await tracker.record_passive_failure("replica1", 2)
    assert tracker.replicas["replica1"].lifecycle is ReplicaLifecycle.UNHEALTHY
    await tracker.set_lifecycle("replica1", ReplicaLifecycle.HALF_OPEN, "health")
    assert await tracker.reserve(1) is None
    await tracker.set_lifecycle("replica1", ReplicaLifecycle.RECOVERING, "probe")
    first = await tracker.reserve(1)
    assert first is not None
    assert await tracker.reserve(1) is None
    await first.release()
    assert not await tracker.record_request_success("replica1", 2)
    assert await tracker.record_request_success("replica1", 2)
    assert tracker.replicas["replica1"].lifecycle is ReplicaLifecycle.HEALTHY
    await tracker.set_lifecycle("replica1", ReplicaLifecycle.RECOVERING, "test")
    assert await tracker.record_passive_failure("replica1", 1)
    assert tracker.replicas["replica1"].lifecycle is ReplicaLifecycle.UNHEALTHY
