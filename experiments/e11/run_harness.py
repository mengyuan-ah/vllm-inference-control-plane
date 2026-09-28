from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import aiohttp
from aiohttp import web

PHASE = Path(__file__).resolve().parents[1]
sys.path.extend([str(PHASE / "gateway"), str(PHASE / "e10")])

# Import by file identity: both experiments intentionally have run_harness.py.
import importlib.util  # noqa: E402

from e10.runtime import FROZEN_BETA, classify_outcome, prompt_record  # noqa: E402
from gateway.app import Gateway, create_app  # noqa: E402
from gateway.config import GatewayConfig, ReplicaConfig  # noqa: E402

_spec = importlib.util.spec_from_file_location("e10_client", PHASE / "e10" / "run_harness.py")
assert _spec and _spec.loader
_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_client)
offer = _client.offer

from e11.protocol import CATALOG, SETTINGS, trace_hash  # noqa: E402
from e11.shim import DelayShim  # noqa: E402


@asynccontextmanager
async def serve(app):
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    finally:
        await runner.cleanup()


class Counter:
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def count(self, prompt):
        return len(self.tokenizer.encode(prompt, add_special_tokens=False))


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def metric(text, name):
    values = [
        float(line.split()[-1])
        for line in text.splitlines()
        if line.startswith(name + "{") or line.startswith(name + " ")
    ]
    if not values:
        raise ValueError(f"missing metric {name}")
    return sum(values)


async def snapshot(session, urls):
    result = {}
    for i, url in enumerate(urls, 1):
        async with session.get(url + "/metrics") as response:
            response.raise_for_status()
            metrics = await response.text()
        async with session.get(url + "/health") as response:
            healthy = response.status == 200
            await response.read()
        result[f"replica{i}"] = dict(
            timestamp=time.monotonic(),
            healthy=healthy,
            raw=metrics,
            waiting=metric(metrics, "vllm:num_requests_waiting"),
            preemptions=metric(metrics, "vllm:num_preemptions_total"),
            cache_hits=metric(metrics, "vllm:prefix_cache_hits_total"),
        )
    return result


async def run_arm(spec, urls, tokenizer, output, source_commit, *, settings=None, stage="measured"):
    settings = dict(SETTINGS if settings is None else settings)
    if spec["arm"] not in {"A", "B"} or spec["target"] not in {"replica1", "replica2"}:
        raise ValueError("invalid arm or target")
    if stage == "measured" and (settings != SETTINGS or spec not in CATALOG):
        raise ValueError("measured settings/catalog are frozen")
    output.mkdir(parents=True, exist_ok=False)
    rows, snapshots, tasks = [], [], []
    shims = [DelayShim(url, f"replica{i}") for i, url in enumerate(urls, 1)]
    gateway, sampler, config = None, None, None
    start, error = None, None
    try:
        prompts = [prompt_record(tokenizer, spec["seed"], i) for i in range(settings["offered"])]
        async with AsyncExitStack() as stack:
            shim_urls = [await stack.enter_async_context(serve(shim.app())) for shim in shims]
            config = GatewayConfig(
                replicas=tuple(
                    ReplicaConfig(
                        f"replica{i}",
                        url,
                        max_inflight=8,
                        max_work=100000,
                        degraded_max_inflight=1,
                        degraded_max_work=20000,
                    )
                    for i, url in enumerate(shim_urls, 1)
                ),
                beta=FROZEN_BETA,
                max_queued_requests=0,
                max_queued_work=1,
                max_queue_residence=0.001,
                routing_mode="round_robin",
                backend_retry_limit=1,
                backend_timeout=5,
                lifecycle_enabled=True,
                slowdown_enabled=spec["arm"] == "B",
            )
            gateway = Gateway(config, token_counter=Counter(tokenizer))
            front = await stack.enter_async_context(serve(create_app(gateway)))
            session = await stack.enter_async_context(
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6))
            )
            snapshots.append(await snapshot(session, urls))

            async def sample():
                while True:
                    await asyncio.sleep(0.25)
                    snapshots.append(await snapshot(session, urls))

            sampler = asyncio.create_task(sample())
            start = time.monotonic()  # After setup, tokenization, and initial metrics.
            target = shims[int(spec["target"][-1]) - 1]
            target.arm(
                start + settings["slowdown_start_s"],
                start + settings["slowdown_end_s"],
                settings["delay_s"],
            )
            try:
                for i, prompt in enumerate(prompts):
                    offset = i / settings["rate"]
                    await asyncio.sleep(max(0, start + offset - time.monotonic()))
                    row = dict(
                        request_id=f"{spec['pair_id']}-{i:04d}",
                        seed=spec["seed"],
                        scheduled_offset_s=offset,
                        arrival_lag_s=time.monotonic() - start - offset,
                        max_tokens=128,
                        **prompt,
                    )
                    rows.append(row)
                    tasks.append(asyncio.create_task(offer(session, front, row)))
                await asyncio.sleep(max(0, start + settings["offer_window_s"] - time.monotonic()))
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
                if sampler.done():
                    sampler.result()
                snapshots.append(await snapshot(session, urls))
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                sampler.cancel()
                await asyncio.gather(sampler, return_exceptions=True)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        for row in rows:
            record = gateway.records.get(row["request_id"]) if gateway else None
            if record:
                row.update(
                    gateway_state=record.state.value,
                    retry_count=record.retry_count,
                    routing_events=record.routing_events,
                )
            row["outcome"] = classify_outcome(row)
        accounting = await gateway.reconcile() if gateway else {}
        manifest = dict(
            **spec,
            source_commit=source_commit,
            settings=settings,
            stage=stage,
            start_monotonic=start,
            end_monotonic=time.monotonic(),
            execution_error=error,
            trace_sha256=trace_hash(rows),
            final_accounting=accounting,
            outcome_schema_version=2,
            backend_urls=urls,
            gateway_config=asdict(config) if config else None,
        )
        write(output / "manifest.json", manifest)
        write(output / "requests.json", rows)
        write(output / "backend_samples.json", snapshots)
        write(output / "detector.json", gateway.slowdown.events if gateway else [])
        write(output / "lifecycle.json", gateway.tracker.lifecycle_events if gateway else [])
        write(output / "injection.json", [event for shim in shims for event in shim.events])
    if error:
        raise RuntimeError(error)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorize-gpu-inference", action="store_true")
    parser.add_argument("--stage", choices=("calibration", "pilot", "measured"), default="measured")
    parser.add_argument("--prerequisite-root", type=Path)
    parser.add_argument("--backend-urls", nargs=2, required=True)
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--arm-id", required=True, choices=[a["arm_id"] for a in CATALOG])
    args = parser.parse_args()
    if not args.authorize_gpu_inference:
        parser.error("GPU inference requires separate explicit authorization; CPU tests use fakes")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if (
        args.source_commit != head
        or subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
    ):
        parser.error("future GPU execution requires a clean reviewed source commit")
    if args.stage == "pilot":
        from qualify import check

        if args.prerequisite_root is None or any(
            f.startswith("calibration:") for f in check(args.prerequisite_root, head)["failures"]
        ):
            parser.error("fixed single-backend calibration must pass before pilot")
    if args.stage == "measured":
        from qualify import check

        if args.prerequisite_root is None or not check(args.prerequisite_root, head)["valid"]:
            parser.error("fixed calibration and paired pilot must pass before measured arms")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        args.tokenizer, local_files_only=True, revision=SETTINGS["revision"]
    )
    spec = dict(next(a for a in CATALOG if a["arm_id"] == args.arm_id))
    settings = dict(SETTINGS)
    if args.stage == "calibration":
        if spec["arm"] != "A" or args.backend_urls[0] != args.backend_urls[1]:
            parser.error("calibration uses arm A and one identical backend URL twice")
        spec.update(
            arm_id="calibration", pair_id="calibration", seed=110000, target="replica1", order=0
        )
        settings.update(
            offered=60, offer_window_s=20.0, slowdown_start_s=100.0, slowdown_end_s=110.0
        )
    elif args.stage == "pilot":
        spec.update(
            arm_id="pilot-" + spec["arm"],
            pair_id="pilot",
            seed=110001,
            target="replica1",
            order=0 if spec["arm"] == "A" else 1,
        )
    asyncio.run(
        run_arm(
            spec,
            args.backend_urls,
            tokenizer,
            args.output,
            args.source_commit,
            settings=settings,
            stage=args.stage,
        )
    )


if __name__ == "__main__":
    main()
