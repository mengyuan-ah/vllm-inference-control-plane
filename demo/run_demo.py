from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TextIO

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.app import Gateway, create_app
from gateway.config import GatewayConfig, ReplicaConfig


class DemoTokenCounter:
    def count(self, prompt: str) -> int:
        return max(1, len(prompt.split()))


class DemoReplica:
    """Deterministic OpenAI-compatible backend used only by the local demo."""

    def __init__(self, replica_id: str):
        self.replica_id = replica_id
        self.requests: list[str] = []
        self.request_started = asyncio.Event()

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/v1/completions", self.completions)
        return app

    async def completions(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        request_id = request.headers.get("X-Request-Id", "unknown")
        self.requests.append(request_id)
        self.request_started.set()
        mode = body.get("demo_mode")
        if mode == "fail_before_content_replica1" and self.replica_id == "replica1":
            return web.json_response({"error": "demo pre-content failure"}, status=503)

        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream"},
        )
        if mode == "fail_after_content_replica1" and self.replica_id == "replica1":
            response.content_length = 10_000
        await response.prepare(request)
        await asyncio.sleep(float(body.get("demo_first_delay", 0)))

        first = {
            "id": "demo",
            "choices": [{"text": "hello", "finish_reason": None}],
            "usage": None,
        }
        await response.write(_event(first))
        if mode == "fail_after_content_replica1" and self.replica_id == "replica1":
            request.transport.close()
            return response

        final = {
            "id": "demo",
            "choices": [{"text": " world", "finish_reason": "length"}],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": body["max_tokens"],
                "total_tokens": 2 + body["max_tokens"],
            },
        }
        await response.write(_event(final))
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response


def _event(payload: dict[str, object]) -> bytes:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


@dataclass
class DemoStack:
    client: TestClient
    gateway: Gateway
    replicas: tuple[DemoReplica, DemoReplica]


@asynccontextmanager
async def demo_stack(
    *,
    routing_mode: str = "round_robin",
    max_inflight: int = 2,
    max_queued_requests: int = 2,
) -> AsyncIterator[DemoStack]:
    replicas = (DemoReplica("replica1"), DemoReplica("replica2"))
    backend_servers = (TestServer(replicas[0].app()), TestServer(replicas[1].app()))
    for server in backend_servers:
        await server.start_server()

    config = GatewayConfig(
        replicas=tuple(
            ReplicaConfig(
                replica.replica_id,
                str(server.make_url("")).rstrip("/"),
                max_inflight=max_inflight,
                max_work=1_000,
            )
            for replica, server in zip(replicas, backend_servers, strict=True)
        ),
        routing_mode=routing_mode,
        max_queued_requests=max_queued_requests,
        max_queued_work=2_000,
        max_queue_residence=1.0,
    )
    gateway = Gateway(config, token_counter=DemoTokenCounter())
    client = TestClient(TestServer(create_app(gateway)))
    await client.start_server()
    try:
        yield DemoStack(client, gateway, replicas)
    finally:
        await client.close()
        for server in backend_servers:
            await server.close()


def _payload(*, max_tokens: int = 2, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": "demo-model",
        "prompt": "hello tokens",
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    payload.update(extra)
    return payload


async def _request(
    stack: DemoStack,
    request_id: str,
    *,
    max_tokens: int = 2,
    **extra: object,
) -> tuple[int, bytes]:
    response = await stack.client.post(
        "/v1/completions",
        json=_payload(max_tokens=max_tokens, **extra),
        headers={"X-Request-Id": request_id},
    )
    return response.status, await response.read()


def _replica_label(replica_id: str | None) -> str:
    return "replica-" + replica_id.removeprefix("replica") if replica_id else "none"


def _write(output: TextIO, line: str = "") -> None:
    print(line, file=output, flush=True)


async def routing_demo(output: TextIO) -> dict[str, object]:
    _write(output, "[1/3] Work-aware routing")
    async with demo_stack(routing_mode="work_aware", max_inflight=3) as stack:
        first_response = await stack.client.post(
            "/v1/completions",
            json=_payload(max_tokens=100, demo_first_delay=0.20),
            headers={"X-Request-Id": "routing-1"},
        )
        await asyncio.wait_for(stack.replicas[0].request_started.wait(), 0.5)
        status, _ = await _request(stack, "routing-2", max_tokens=10)
        assert status == 200

        before_third = await stack.gateway.tracker.snapshot()
        round_robin_candidate = stack.gateway.tracker.order[stack.gateway.tracker.cursor]
        status, _ = await _request(stack, "routing-3", max_tokens=10)
        assert status == 200
        await first_response.read()

        records = stack.gateway.records
        assert records["routing-1"].assigned_replica == "replica1"
        assert records["routing-2"].assigned_replica == "replica2"
        assert records["routing-3"].assigned_replica == "replica2"
        assert round_robin_candidate == "replica1"
        _write(
            output,
            f"request-1  work={records['routing-1'].total_work:.0f}  "
            f"-> {_replica_label(records['routing-1'].assigned_replica)}",
        )
        _write(
            output,
            f"request-2  work={records['routing-2'].total_work:.0f}   "
            f"-> {_replica_label(records['routing-2'].assigned_replica)}",
        )
        _write(
            output,
            f"request-3  work={records['routing-3'].total_work:.0f}   "
            f"-> {_replica_label(records['routing-3'].assigned_replica)}",
        )
        _write(output)
        _write(output, "Outstanding work before request-3:")
        for replica_id in stack.gateway.tracker.order:
            work = before_third[replica_id]["outstanding_work"]
            _write(output, f"  {_replica_label(replica_id)}: {work:.0f}")
        _write(
            output,
            f"Next round-robin candidate: {_replica_label(round_robin_candidate)}; "
            "work-aware selected replica-2",
        )
        _write(output, "\n✓ work-aware routing exercised\n")
        return await stack.gateway.reconcile()


async def overload_demo(output: TextIO) -> dict[str, object]:
    _write(output, "[2/3] Overload protection")
    offered = 8
    async with demo_stack(max_inflight=1, max_queued_requests=2) as stack:
        results = await asyncio.gather(
            *(
                _request(
                    stack,
                    f"overload-{index}",
                    demo_first_delay=0.15,
                )
                for index in range(offered)
            )
        )
        admitted = sum(status == 200 for status, _ in results)
        rejected = sum(status == 429 for status, _ in results)
        assert (admitted, rejected) == (4, 4)
        _write(output, f"offered:   {offered} requests")
        _write(output, f"admitted:  {admitted}")
        _write(output, f"rejected:  {rejected}")
        _write(output, "status:    429 queue_request_limit")
        _write(output, "\n✓ bounded admission protected serving capacity\n")
        return await stack.gateway.reconcile()


async def failover_demo(output: TextIO) -> dict[str, object]:
    _write(output, "[3/3] Streaming failover")
    async with demo_stack() as stack:
        status, body = await _request(
            stack,
            "pre-content-failure",
            demo_mode="fail_before_content_replica1",
        )
        record = stack.gateway.records["pre-content-failure"]
        assert status == 200 and b"hello" in body and b" world" in body
        assert record.retry_count == 1
        assert stack.replicas[0].requests == ["pre-content-failure"]
        assert stack.replicas[1].requests == ["pre-content-failure"]
        _write(output, "request -> replica-1")
        _write(output, "replica-1 fails before first content")
        _write(output, "retry   -> replica-2")
        _write(output, '\nclient received: "hello world"')

    async with demo_stack() as stack:
        status, body = await _request(
            stack,
            "post-content-failure",
            demo_mode="fail_after_content_replica1",
        )
        record = stack.gateway.records["post-content-failure"]
        assert status == 200 and b"backend_stream_failed" in body
        assert record.retry_count == 0
        assert stack.replicas[1].requests == []
        _write(output, "retries after client-visible content: 0")
        _write(output, "\n✓ safe pre-content failover; no unsafe replay\n")
        return await stack.gateway.reconcile()


def _assert_drained(snapshot: dict[str, object]) -> None:
    queue = snapshot["queue"]
    replicas = snapshot["replicas"]
    assert queue["queued_requests"] == 0
    assert all(replica["active_reservations"] == 0 for replica in replicas.values())
    assert all(replica["outstanding_work"] == 0 for replica in replicas.values())


async def run_demo(output: TextIO = sys.stdout) -> None:
    _write(output, "vLLM Control Plane Demo")
    _write(output, "=======================\n")
    snapshots = [
        await routing_demo(output),
        await overload_demo(output),
        await failover_demo(output),
    ]
    for snapshot in snapshots:
        _assert_drained(snapshot)
    final = snapshots[-1]
    _write(output, "Final accounting:")
    _write(output, f"  queued_requests: {final['queue']['queued_requests']}")
    active = sum(item["active_reservations"] for item in final["replicas"].values())
    outstanding = sum(item["outstanding_work"] for item in final["replicas"].values())
    _write(output, f"  active_reservations: {active}")
    _write(output, f"  outstanding_work: {outstanding:.0f}")
    _write(output, "\nDemo complete.")


if __name__ == "__main__":
    asyncio.run(run_demo())
