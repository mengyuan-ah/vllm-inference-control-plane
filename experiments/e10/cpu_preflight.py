from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from aiohttp import ClientSession, web

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gateway.app import Gateway, create_app  # noqa: E402
from gateway.config import GatewayConfig, ReplicaConfig  # noqa: E402
from gateway.models import RequestState  # noqa: E402

from e10.injector import hard_fail, register  # noqa: E402


class Counter:
    def count(self, prompt: str) -> int:
        return len(prompt)


def port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def launch(replica_port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "e10.fake_replica", "--port", str(replica_port)],
        cwd=Path(__file__).parent,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def wait_health(url: str, max_wait: float = 3.0) -> None:
    deadline = time.monotonic() + max_wait
    async with ClientSession() as session:
        while time.monotonic() < deadline:
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        return
            except Exception:
                pass
            await asyncio.sleep(0.02)
    raise TimeoutError(url)


async def run(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)  # noqa: ASYNC240
    p1_port, p2_port = port(), port()
    p1, p2 = launch(p1_port), launch(p2_port)
    owned = [p1, p2]
    timeline: list[dict] = []
    try:
        await wait_health(f"http://127.0.0.1:{p1_port}/health")
        await wait_health(f"http://127.0.0.1:{p2_port}/health")
        identity_path = output / "replica1-generation-1.json"
        item = register(identity_path, "replica1", p1.pid, "fake-cpu", [p1_port], 1)
        config = GatewayConfig(
            replicas=(
                ReplicaConfig(
                    "replica1",
                    f"http://127.0.0.1:{p1_port}",
                    max_inflight=4,
                    recovery_max_inflight=1,
                ),
                ReplicaConfig(
                    "replica2",
                    f"http://127.0.0.1:{p2_port}",
                    max_inflight=4,
                    recovery_max_inflight=1,
                ),
            ),
            max_queued_requests=0,
            max_queued_work=1,
            max_queue_residence=0.01,
            backend_timeout=0.4,
            backend_retry_limit=1,
            beta=107.2820167,
            prediction_admission_mode="disabled",
            lifecycle_enabled=True,
            health_probe_interval=0.03,
            health_probe_timeout=0.02,
            health_failure_threshold=2,
            health_success_threshold=2,
            passive_failure_threshold=1,
            recovery_success_threshold=4,
            inference_probe_timeout=0.4,
        )
        gateway = Gateway(config, token_counter=Counter())
        runner = web.AppRunner(create_app(gateway))
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        gateway_port = site._server.sockets[0].getsockname()[1]
        start = time.monotonic()
        killed = restarted = False
        sent = 0
        async with ClientSession() as client:
            while time.monotonic() - start < 1.6:
                elapsed = time.monotonic() - start
                if not killed and elapsed >= 0.30:
                    timeline.append(
                        {
                            "event": "fault_scheduled",
                            "scheduled_offset_s": 0.30,
                            "timestamp": time.monotonic(),
                        }
                    )
                    timeline.append(hard_fail(item))
                    killed = True
                if killed and not restarted and elapsed >= 0.80:
                    p1 = launch(p1_port)
                    owned.append(p1)
                    await wait_health(f"http://127.0.0.1:{p1_port}/health")
                    register(
                        output / "replica1-generation-2.json",
                        "replica1",
                        p1.pid,
                        "fake-cpu",
                        [p1_port],
                        2,
                    )
                    timeline.append(
                        {"event": "restart_invoked", "generation": 2, "timestamp": time.monotonic()}
                    )
                    restarted = True
                body = {
                    "model": "fake",
                    "prompt": "x" * 1024,
                    "max_tokens": 128,
                    "temperature": 0,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "ignore_eos": True,
                    "seed": 101001,
                }
                try:
                    async with client.post(
                        f"http://127.0.0.1:{gateway_port}/v1/completions",
                        json=body,
                        headers={"X-Request-Id": f"e10-preflight-{sent:03d}"},
                    ) as response:
                        await response.read()
                except Exception:
                    pass
                sent += 1
                await asyncio.sleep(0.02)
        await asyncio.sleep(0.15)
        accounting = await gateway.reconcile()
        records = list(gateway.records.values())
        result = {
            "valid": (
                killed
                and restarted
                and all(
                    item["inflight"] == 0 and item["outstanding_work"] == 0
                    for item in accounting["replicas"].values()
                )
                and any(event["to"] == "Unhealthy" for event in accounting["lifecycle_events"])
                and any(event["to"] == "HalfOpen" for event in accounting["lifecycle_events"])
                and any(event["to"] == "Recovering" for event in accounting["lifecycle_events"])
                and accounting["replicas"]["replica1"]["lifecycle"] == "Healthy"
            ),
            "offered": sent,
            "completed": sum(record.state is RequestState.COMPLETED for record in records),
            "failed": sum(record.state is RequestState.FAILED for record in records),
            "rejected": sum(record.state.value.startswith("Rejected") for record in records),
            "retried": sum(record.retry_count for record in records),
            "timeline": timeline,
            "accounting": accounting,
        }
        (output / "preflight.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        await runner.cleanup()
        return result
    finally:
        for process in owned:
            if process.poll() is None:
                os.killpg(process.pid, 15)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, 9)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.output))
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["valid"] else 2)


if __name__ == "__main__":
    main()
