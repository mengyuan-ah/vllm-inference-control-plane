from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web
from transformers import AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gateway.app import Gateway, create_app  # noqa: E402
from gateway.config import GatewayConfig, ReplicaConfig  # noqa: E402
from gateway.tokenizers import HuggingFaceTokenCounter  # noqa: E402

from e10.cleanup import terminate_owned
from e10.injector import hard_fail, launch_restart, load, register_after_exec  # noqa: E402
from e10.runtime import (  # noqa: E402
    FROZEN_BETA,
    MODEL,
    MODEL_REVISION,
    OUTPUT_TOKENS,
    RECOVERY_MAX_WORK,
    StreamObservation,
    classify_outcome,
    derived_offer_count,
    prompt_record,
)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(value, sort_keys=True) + "\n" for value in values))


async def offer(session: aiohttp.ClientSession, gateway_url: str, row: dict[str, Any]) -> None:
    sent = time.perf_counter()
    row["actual_send_monotonic"] = sent
    observation = StreamObservation()
    try:
        async with session.post(
            f"{gateway_url}/v1/completions",
            json={
                "model": MODEL,
                "prompt": row["prompt"],
                "max_tokens": OUTPUT_TOKENS,
                "temperature": 0,
                "stream": True,
                "stream_options": {"include_usage": True},
                "ignore_eos": True,
                "seed": 42,
            },
            headers={"X-Request-Id": row["request_id"]},
        ) as response:
            row["status"] = response.status
            if response.status == 200:
                buffer = ""
                async for raw in response.content.iter_any():
                    buffer += raw.decode(errors="replace")
                    while "\n\n" in buffer:
                        block, buffer = buffer.split("\n\n", 1)
                        for line in block.splitlines():
                            if line.startswith("data:"):
                                observation.feed(line[5:].strip(), time.perf_counter())
            else:
                row["response_body"] = (await response.text())[:1000]
    except TimeoutError as exc:
        observation.transport_error = True
        row.update(status=None, timed_out=True, error=f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        observation.transport_error = True
        row.update(status=None, error=f"{type(exc).__name__}: {exc}")
    except asyncio.CancelledError:
        observation.transport_error = True
        row["client_cancelled"] = True
        raise
    finally:
        completed = time.perf_counter()
        row.update(observation.metrics(sent, completed))
        row["completion_monotonic"] = completed


async def run(args: argparse.Namespace, *, tokenizer: Any | None = None) -> dict[str, Any]:
    args.offers = derived_offer_count(args.rate, args.offer_window, args.offers)
    args.output.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, Any]] = []
    tasks: list[asyncio.Task[None]] = []
    timeline: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    accounting: dict[str, Any] = {}
    execution_error: str | None = None
    restarted_registry: Path | None = None
    runner: web.AppRunner | None = None
    controller: asyncio.Task[None] | None = None
    gateway: Gateway | None = None
    start = 0.0
    try:
        tokenizer = tokenizer or AutoTokenizer.from_pretrained(
            args.tokenizer, revision=MODEL_REVISION, local_files_only=True
        )
        prompts = [prompt_record(tokenizer, args.seed, index) for index in range(args.offers)]
        if any(item["input_tokens"] != 1024 for item in prompts):
            raise RuntimeError("prompt verification failed before traffic")
        replicas = tuple(
            ReplicaConfig(
                f"replica{index + 1}",
                url,
                recovery_max_work=RECOVERY_MAX_WORK,
            )
            for index, url in enumerate((args.replica1_url, args.replica2_url))
        )
        config = GatewayConfig(
            replicas=replicas,
            max_queued_requests=0,
            max_queued_work=1,
            max_queue_residence=0.001,
            backend_timeout=args.client_timeout,
            backend_retry_limit=1,
            beta=FROZEN_BETA,
            routing_mode="round_robin",
            prediction_admission_mode="disabled",
            lifecycle_enabled=True,
        )
        gateway = Gateway(config, token_counter=HuggingFaceTokenCounter(str(args.tokenizer)))
        runner = web.AppRunner(create_app(gateway))
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        gateway_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

        async def fault_controller() -> None:
            await asyncio.sleep(max(0, start + args.fault_at - time.perf_counter()))
            timeline.append({"event": "fault_marker", "actual": time.perf_counter()})
            if args.condition == "fault":
                timeline.append(hard_fail(load(args.registry)))
            await asyncio.sleep(max(0, start + args.restart_at - time.perf_counter()))
            timeline.append({"event": "restart_marker", "actual": time.perf_counter()})
            if args.condition == "fault":
                command = json.loads(args.restart_command_json)
                process = launch_restart(command, args.gpu, args.restart_log)
                try:
                    await register_after_exec(
                        args.restart_registry,
                        args.target,
                        process.pid,
                        args.gpu,
                        args.ports,
                        args.restart_generation,
                        command,
                    )
                except Exception:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    raise
                nonlocal restarted_registry
                restarted_registry = args.restart_registry
                timeline.append(
                    {
                        "event": "restart_invocation",
                        "pid": process.pid,
                        "actual": time.perf_counter(),
                    }
                )

        timeout = aiohttp.ClientTimeout(total=args.client_timeout)
        async with aiohttp.ClientSession(
            timeout=timeout, connector=aiohttp.TCPConnector(limit=0)
        ) as session:
            for url in {args.replica1_url, args.replica2_url}:
                async with session.get(f"{url}/health") as response:
                    if response.status != 200:
                        raise RuntimeError(f"backend is not ready before arm: {url}")
            start = time.perf_counter()
            controller = asyncio.create_task(fault_controller())
            for index, prompt in enumerate(prompts):
                offset = index / args.rate
                await asyncio.sleep(max(0, start + offset - time.perf_counter()))
                now = time.perf_counter()
                row = {
                    "request_id": f"e10-{args.seed}-{index:04d}",
                    "scheduled_offset_s": offset,
                    "arrival_lag_s": now - (start + offset),
                    "max_tokens": OUTPUT_TOKENS,
                    "seed": args.seed,
                    "phase": "pre_fault"
                    if offset < args.fault_at
                    else "fault"
                    if offset < args.restart_at
                    else "post_restart",
                    **prompt,
                }
                rows.append(row)
                tasks.append(asyncio.create_task(offer(session, gateway_url, row)))
            await asyncio.sleep(max(0, start + args.offer_window - time.perf_counter()))
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=args.drain_timeout)
            await controller
            accounting = await gateway.reconcile()
    except Exception as exc:
        execution_error = f"{type(exc).__name__}: {exc}"
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if controller is not None and not controller.done():
            controller.cancel()
            await asyncio.gather(controller, return_exceptions=True)
        if gateway is not None:
            accounting = await gateway.reconcile()
            for request_id, record in gateway.records.items():
                for event in record.routing_events:
                    if event.get("event") == "backend_dispatch":
                        attempts.append({"request_id": request_id, **event})
                row = next((item for item in rows if item["request_id"] == request_id), None)
                if row is not None:
                    row["retry_count"] = record.retry_count
                    row["gateway_state"] = record.state.value
                    row["routing_events"] = record.routing_events
                    row["outcome"] = classify_outcome(row)
        for row in rows:
            if "outcome" not in row:
                row["outcome"] = classify_outcome(row)
        if runner is not None:
            await runner.cleanup()
        cleanup = None
        if execution_error and restarted_registry is not None:
            cleanup = terminate_owned(load(restarted_registry))
            timeline.append(cleanup)
        rows.sort(key=lambda row: row["request_id"])
        write_jsonl(args.output / "requests.jsonl", rows)
        write_jsonl(args.output / "attempts.jsonl", attempts)
        write_json(args.output / "timeline.json", timeline)
        manifest = {
            "outcome_schema_version": 2,
            "arm_id": args.arm_id,
            "pair_id": args.pair_id,
            "condition": args.condition,
            "offered": args.offers,
            "rate": args.rate,
            "offer_window_s": args.offer_window,
            "source_commit": args.source_commit,
            "model": MODEL,
            "model_revision": MODEL_REVISION,
            "input_tokens": 1024,
            "max_tokens": OUTPUT_TOKENS,
            "request_work": 1024 + FROZEN_BETA * OUTPUT_TOKENS,
            "recovery_max_work": RECOVERY_MAX_WORK,
            "execution_error": execution_error,
            "completed_rows": len(rows),
            "slo_success_count": sum(bool(row.get("slo_success")) for row in rows),
            "final_accounting": accounting,
        }
        write_json(args.output / "manifest.json", manifest)
    if execution_error:
        raise RuntimeError(execution_error)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", choices=("control", "fault"), required=True)
    parser.add_argument("--replica1-url", required=True)
    parser.add_argument("--replica2-url", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arm-id", required=True)
    parser.add_argument("--pair-id", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--rate", type=float, required=True)
    parser.add_argument("--offers", type=int)
    parser.add_argument("--offer-window", type=float, default=60)
    parser.add_argument("--fault-at", type=float, default=10)
    parser.add_argument("--restart-at", type=float, default=25)
    parser.add_argument("--client-timeout", type=float, default=5)
    parser.add_argument("--drain-timeout", type=float, default=120)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--restart-registry", type=Path)
    parser.add_argument("--restart-log", type=Path)
    parser.add_argument("--restart-generation", type=int)
    parser.add_argument("--restart-command-json")
    parser.add_argument("--target", default="replica1")
    parser.add_argument("--gpu", default="0")
    parser.add_argument("--ports", type=int, nargs="+", default=[8101])
    args = parser.parse_args()
    if args.condition == "fault" and not all(
        (
            args.registry,
            args.restart_registry,
            args.restart_log,
            args.restart_generation,
            args.restart_command_json,
        )
    ):
        parser.error(
            "fault condition requires registry, restart-registry, restart-log, "
            "restart-generation, and restart-command-json"
        )
    print(json.dumps(asyncio.run(run(args)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
