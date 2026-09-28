from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from aiohttp import ClientError, ClientSession

from .config import GatewayConfig
from .models import ReplicaLifecycle
from .tracker import ReplicaTracker


class ReplicaLifecycleManager:
    """E10 active health and inference-readiness state machine.

    Disabled by default.  Health success can open the circuit, but only a
    separately-accounted inference probe can enter Recovering.  Normal request
    successes then earn full Healthy eligibility.
    """

    def __init__(self, config: GatewayConfig, tracker: ReplicaTracker):
        self.config = config
        self.tracker = tracker
        self.session: ClientSession | None = None
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.probe_tasks: dict[str, asyncio.Task[None]] = {}
        self.events: list[dict[str, Any]] = []
        self._closed = False

    async def start(self) -> None:
        if self.tasks:
            return
        self._closed = False
        self.session = ClientSession()
        for replica_id in self.tracker.order:
            self.tasks[replica_id] = asyncio.create_task(
                self._health_loop(replica_id), name=f"health-{replica_id}"
            )

    async def close(self) -> None:
        self._closed = True
        tasks = [*self.tasks.values(), *self.probe_tasks.values()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.tasks.clear()
        self.probe_tasks.clear()
        if self.session is not None:
            await self.session.close()
            self.session = None

    def _event(self, event: str, replica_id: str, **fields: Any) -> None:
        self.events.append(
            {
                "event": event,
                "timestamp": time.monotonic(),
                "replica_id": replica_id,
                **fields,
            }
        )

    async def _health_loop(self, replica_id: str) -> None:
        failures = 0
        successes = 0
        while not self._closed:
            ok = await self._health_once(replica_id)
            failures = 0 if ok else failures + 1
            successes = successes + 1 if ok else 0
            state = self.tracker.replicas[replica_id].lifecycle
            if (
                state
                in {
                    ReplicaLifecycle.HEALTHY,
                    ReplicaLifecycle.DEGRADED,
                    ReplicaLifecycle.RECOVERING,
                }
                and failures >= self.config.health_failure_threshold
            ):
                await self.tracker.set_lifecycle(
                    replica_id, ReplicaLifecycle.UNHEALTHY, "active_health_failure_threshold"
                )
                successes = 0
            elif (
                state is ReplicaLifecycle.UNHEALTHY
                and successes >= self.config.health_success_threshold
            ):
                changed = await self.tracker.set_lifecycle(
                    replica_id, ReplicaLifecycle.HALF_OPEN, "active_health_success_threshold"
                )
                if changed:
                    self._launch_probe(replica_id)
            elif state is ReplicaLifecycle.HALF_OPEN and ok:
                self._launch_probe(replica_id)
            await asyncio.sleep(self.config.health_probe_interval)

    async def _health_once(self, replica_id: str) -> bool:
        assert self.session is not None
        endpoint = self.tracker.replicas[replica_id].config.endpoint
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.config.health_probe_timeout):
                response = await self.session.get(f"{endpoint}/health")
                ok = response.status == 200
                await response.read()
        except (TimeoutError, ClientError, ConnectionError):
            ok = False
        self._event(
            "active_health_probe", replica_id, success=ok, duration=time.monotonic() - started
        )
        return ok

    def _launch_probe(self, replica_id: str) -> None:
        previous = self.probe_tasks.get(replica_id)
        if previous is not None and not previous.done():
            return
        self.probe_tasks[replica_id] = asyncio.create_task(
            self._inference_probe(replica_id), name=f"inference-probe-{replica_id}"
        )

    async def _inference_probe(self, replica_id: str) -> None:
        if not await self.tracker.begin_half_open_probe(replica_id):
            return
        assert self.session is not None
        endpoint = self.tracker.replicas[replica_id].config.endpoint
        generation = await self.tracker.new_generation(replica_id)
        self._event("inference_probe_start", replica_id, generation=generation)
        ok = False
        reason = "unknown"
        try:
            body = {
                "model": "Qwen/Qwen3-4B-Instruct-2507",
                "prompt": "E10 readiness probe",
                "max_tokens": self.config.inference_probe_max_tokens,
                "temperature": 0,
                "stream": True,
                "stream_options": {"include_usage": True},
                "ignore_eos": True,
                "seed": 42,
            }
            async with asyncio.timeout(self.config.inference_probe_timeout):
                response = await self.session.post(f"{endpoint}/v1/completions", json=body)
                text = await response.text()
            ok = response.status == 200 and "data: [DONE]" in text and '"usage"' in text
            reason = "complete_stream" if ok else "incomplete_or_http_error"
        except (TimeoutError, ClientError, ConnectionError) as exc:
            reason = type(exc).__name__
        finally:
            await self.tracker.end_half_open_probe(replica_id)
        self._event(
            "inference_probe_complete",
            replica_id,
            generation=generation,
            success=ok,
            reason=reason,
        )
        if ok:
            await self.tracker.set_lifecycle(
                replica_id, ReplicaLifecycle.RECOVERING, "inference_probe_success"
            )
        else:
            await self.tracker.set_lifecycle(
                replica_id, ReplicaLifecycle.UNHEALTHY, "inference_probe_failure"
            )

    async def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.config.lifecycle_enabled,
            "events": [dict(event) for event in self.events],
            "tasks": sorted(name for name, task in self.tasks.items() if not task.done()),
            "probe_tasks": sorted(
                name for name, task in self.probe_tasks.items() if not task.done()
            ),
        }
