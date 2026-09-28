from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiohttp import web
from gateway.app import Gateway
from transformers import AutoTokenizer

import pilot_harness
from e5.trace import FROZEN_BETA, PILOT_SEEDS, generate_trace

EXPECTED_BETA = 107.2820167
PILOT_COUNT = 64
GATEWAY_MODULE = str(Path(sys.modules[Gateway.__module__].__file__).resolve())
HARNESS_MODULE = str(Path(pilot_harness.__file__).resolve())


class FakeBackend:
    def __init__(self, tokenizer: Any):
        self.tokenizer = tokenizer
        self.requests = 0

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/health", self.health)
        app.router.add_get("/metrics", self.metrics)
        app.router.add_post("/v1/completions", self.completions)
        return app

    async def health(self, _request: web.Request) -> web.Response:
        return web.Response()

    async def metrics(self, _request: web.Request) -> web.Response:
        return web.Response(
            text=(
                "vllm:num_requests_running 0\n"
                "vllm:num_requests_waiting 0\n"
                "vllm:num_preemptions_total 0\n"
                "vllm:prefix_cache_hits_total 0\n"
            )
        )

    async def completions(self, request: web.Request) -> web.StreamResponse:
        self.requests += 1
        body = await request.json()
        prompt_tokens = len(self.tokenizer.encode(body["prompt"], add_special_tokens=False))
        completion_tokens = int(body["max_tokens"])
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        first = {"choices": [{"text": "x", "finish_reason": None}], "usage": None}
        final = {
            "choices": [{"text": "", "finish_reason": "length"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        await response.write(f"data: {json.dumps(first)}\n\n".encode())
        await response.write(f"data: {json.dumps(final)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response


async def start_backend(backend: FakeBackend) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(backend.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server = site._server
    assert server is not None and server.sockets
    port = server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def initialize_metadata(output_root: Path, tokenizer: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=False)
    metadata = {
        "beta": FROZEN_BETA,
        "pilot_count": PILOT_COUNT,
        "pilot_seeds": sorted(PILOT_SEEDS),
        "python": sys.executable,
        "working_directory": str(Path.cwd()),
        "harness": str(Path(pilot_harness.__file__).resolve()),
        "gateway": str(Path(sys.modules[Gateway.__module__].__file__).resolve()),
        "tokenizer": str(tokenizer.resolve()),
    }
    path = output_root / "preflight-metadata.json"
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return path


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if FROZEN_BETA != EXPECTED_BETA:
        raise RuntimeError(f"frozen beta changed: {FROZEN_BETA!r}")
    if not args.tokenizer.is_dir():
        raise RuntimeError(f"tokenizer path is not a directory: {args.tokenizer}")
    generate_trace("mixed", request_count=PILOT_COUNT, rate=2.5, seed=51001, stage="pilot")
    for invalid_seed in (0, 52001):
        try:
            generate_trace(
                "mixed", request_count=PILOT_COUNT, rate=2.5, seed=invalid_seed, stage="pilot"
            )
        except ValueError:
            pass
        else:
            raise RuntimeError(f"non-pilot seed accepted: {invalid_seed}")

    metadata_path = initialize_metadata(args.output_root, args.tokenizer)
    try:
        initialize_metadata(args.output_root, args.tokenizer)
    except FileExistsError:
        pass
    else:
        raise RuntimeError("output collision protection did not fail closed")

    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
    backends = (FakeBackend(tokenizer), FakeBackend(tokenizer))
    runners_and_urls = [await start_backend(backend) for backend in backends]
    runners = [item[0] for item in runners_and_urls]
    urls = tuple(item[1] for item in runners_and_urls)
    smoke_root = args.output_root / "cpu-smoke"
    smoke_args = SimpleNamespace(
        stage="smoke",
        rate=100.0,
        count=4,
        seed=51004,
        tokenizer=args.tokenizer,
        output_root=smoke_root,
        backend_urls=urls,
        gateway_port=0,
    )
    smoke_root.mkdir()
    try:
        summaries = []
        for policy in ("round_robin", "work_aware"):
            summaries.append(await pilot_harness.run_arm(smoke_args, policy))
    finally:
        for runner in runners:
            await runner.cleanup()

    if any(summary["successes"] != 4 for summary in summaries):
        raise RuntimeError("CPU smoke did not complete every request")
    if any(summary["final_accounting"]["queue"]["queued_requests"] for summary in summaries):
        raise RuntimeError("CPU smoke left queued requests")
    for summary in summaries:
        if not summary["validity"]["infrastructure_valid"]:
            raise RuntimeError(f"CPU smoke validity failed: {summary['validity']}")
        for replica in summary["final_accounting"]["replicas"].values():
            if replica["inflight"] or replica["active_reservations"] or replica["outstanding_work"]:
                raise RuntimeError("CPU smoke left outstanding replica accounting")
    result = {
        "status": "ok",
        "beta": FROZEN_BETA,
        "pilot_count": PILOT_COUNT,
        "pilot_seeds": sorted(PILOT_SEEDS),
        "metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
        "gateway_module": GATEWAY_MODULE,
        "harness_module": HARNESS_MODULE,
        "backend_requests": [backend.requests for backend in backends],
        "summaries": summaries,
    }
    (args.output_root / "preflight-result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="E5 zero-GPU import and CPU smoke gate")
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
