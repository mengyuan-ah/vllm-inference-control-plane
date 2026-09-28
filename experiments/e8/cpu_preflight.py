#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from aiohttp import ClientSession, web

GATEWAY_ROOT = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(GATEWAY_ROOT))

from gateway.app import Gateway, create_app  # noqa: E402
from gateway.config import GatewayConfig, ReplicaConfig  # noqa: E402
from gateway.prediction import CountAdmissionPolicy  # noqa: E402


class Counter:
    def count(self, _prompt: str) -> int:
        return 1


async def run_case(configuration: str) -> dict:
    hits = [0, 0]
    releases = [asyncio.Event(), asyncio.Event()]
    runners: list[web.AppRunner] = []
    urls: list[str] = []
    for index in range(2):

        async def completion(request: web.Request, replica: int = index) -> web.StreamResponse:
            hits[replica] += 1
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await releases[replica].wait()
            event = {"choices": [{"text": "ok", "finish_reason": "length"}]}
            await response.write(f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode())
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_post("/v1/completions", completion)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runners.append(runner)
        urls.append(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")

    gateway = Gateway(
        GatewayConfig(
            replicas=tuple(
                ReplicaConfig(f"r{i + 1}", url, max_inflight=16) for i, url in enumerate(urls)
            ),
            max_queued_requests=0,
            prediction_admission_mode="shadow" if configuration == "A" else "enforce",
            prediction_admission_count_threshold=1,
        ),
        token_counter=Counter(),
        prediction_admission_policy=CountAdmissionPolicy(1),
    )
    gateway_runner = web.AppRunner(create_app(gateway))
    await gateway_runner.setup()
    gateway_site = web.TCPSite(gateway_runner, "127.0.0.1", 0)
    await gateway_site.start()
    port = gateway_site._server.sockets[0].getsockname()[1]
    payload = {"model": "fake", "prompt": "x", "max_tokens": 1, "stream": True}
    try:
        async with ClientSession() as session:
            first = asyncio.create_task(
                session.post(
                    f"http://127.0.0.1:{port}/v1/completions",
                    json=payload,
                    headers={"X-Request-Id": f"{configuration}-1"},
                )
            )
            second = asyncio.create_task(
                session.post(
                    f"http://127.0.0.1:{port}/v1/completions",
                    json=payload,
                    headers={"X-Request-Id": f"{configuration}-2"},
                )
            )
            for _ in range(100):
                if (
                    all(
                        any(
                            event["event"] == "backend_dispatch"
                            for event in gateway.records[key].routing_events
                        )
                        for key in (f"{configuration}-1", f"{configuration}-2")
                        if key in gateway.records
                    )
                    and len(gateway.records) >= 2
                ):
                    break
                await asyncio.sleep(0.005)
            assert len(gateway.records) == 2
            third = asyncio.create_task(
                session.post(
                    f"http://127.0.0.1:{port}/v1/completions",
                    json=payload,
                    headers={"X-Request-Id": f"{configuration}-3"},
                )
            )
            if configuration == "A":
                for _ in range(100):
                    record = gateway.records.get(f"{configuration}-3")
                    if record and any(
                        event["event"] == "backend_dispatch" for event in record.routing_events
                    ):
                        break
                    await asyncio.sleep(0.005)
                third_response = await third
                assert third_response.status == 200
                assert any(
                    event["event"] == "backend_dispatch"
                    for event in gateway.records[f"{configuration}-3"].routing_events
                )
            else:
                response = await third
                assert response.status == 429 and sum(hits) == 2
                assert (await response.json())["error"]["code"] == "prediction_count_threshold"
            for event in releases:
                event.set()
            responses = await asyncio.gather(
                first, second, *([third] if configuration == "A" else [])
            )
            for response in responses:
                await response.read()
                assert response.status == 200
        accounting = await gateway.reconcile()
        assert all(
            not row["inflight"] and not row["active_reservations"] and not row["outstanding_work"]
            for row in accounting["replicas"].values()
        )
        return {
            "configuration": configuration,
            "backend_dispatches": sum(hits),
            "accounting": accounting,
        }
    finally:
        for event in releases:
            event.set()
        await gateway_runner.cleanup()
        for runner in runners:
            await runner.cleanup()


async def main(output_root: Path) -> None:
    await asyncio.to_thread(output_root.mkdir, parents=True, exist_ok=False)
    results = [await run_case("A"), await run_case("B")]
    await asyncio.to_thread(
        (output_root / "preflight.json").write_text,
        json.dumps(results, indent=2) + "\n",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    asyncio.run(main(parser.parse_args().output_root))
