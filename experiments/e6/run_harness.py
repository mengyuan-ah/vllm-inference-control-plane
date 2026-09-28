#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import aiohttp
import numpy as np
from aiohttp import web
from gateway.app import Gateway, create_app
from gateway.config import GatewayConfig, ReplicaConfig
from gateway.tokenizers import HuggingFaceTokenCounter
from transformers import AutoTokenizer

from e6.metrics import (
    classify,
    clean_response_success,
    is_slo_success,
    percentile,
    validity_reasons,
)
from e6.trace import MEASURED_SEEDS, PILOT_SEEDS, generate_trace, trace_sha256

MODEL = "Qwen/Qwen3-4B-Instruct-2507"
MODEL_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
FROZEN_BETA = 107.2820167


def generate_prompt(tokenizer: Any, length: int, seed: int) -> str:
    rng = np.random.default_rng(seed)
    prohibited = set(tokenizer.all_special_ids)
    allowed = np.array([token for token in range(tokenizer.vocab_size) if token not in prohibited])
    tokens = allowed[rng.integers(0, len(allowed), size=length)].tolist()
    for _ in range(20):
        prompt = tokenizer.decode(tokens, skip_special_tokens=False)
        encoded = tokenizer.encode(prompt, add_special_tokens=False)
        if len(encoded) == length:
            return prompt
        tokens = encoded[:length] if len(encoded) > length else encoded + allowed[
            rng.integers(0, len(allowed), size=length - len(encoded))
        ].tolist()
    raise RuntimeError("unable to generate exact-token prompt")


def configuration(args: argparse.Namespace) -> tuple[int, int, float]:
    if args.configuration == "A":
        return 128, 0, args.queue_deadline
    if args.configuration == "B":
        return args.request_cap, 0, args.queue_deadline
    return args.request_cap, args.queue_size, args.queue_deadline


async def fetch_metrics(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(f"{url}/metrics") as response:
        response.raise_for_status()
        return await response.text()


def metric_sum(text: str, name: str) -> float:
    return sum(
        float(line.rsplit(" ", 1)[-1])
        for line in text.splitlines()
        if line.startswith(name + "{") or line.startswith(name + " ")
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    arm_id = f"e6-{args.stage}-r{args.rate:g}-{args.configuration}-s{args.seed}"
    arm_dir = args.output_root / arm_id
    arm_dir.mkdir(parents=True, exist_ok=False)
    trace = generate_trace(count=args.count, rate=args.rate, seed=args.seed, stage=args.stage)
    (arm_dir / "trace.json").write_text(json.dumps(trace, indent=2) + "\n")
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
    prompts = [generate_prompt(tokenizer, 1024, row["prompt_seed"]) for row in trace]
    if any(len(tokenizer.encode(prompt, add_special_tokens=False)) != 1024 for prompt in prompts):
        raise RuntimeError("prompt token verification failed")

    request_cap, queue_size, queue_deadline = configuration(args)
    gateway = Gateway(
        GatewayConfig(
            replicas=tuple(
                ReplicaConfig(
                    f"replica{index + 1}",
                    url,
                    max_inflight=request_cap,
                    max_work=1e9,
                    recovery_max_inflight=request_cap,
                    recovery_max_work=1e9,
                )
                for index, url in enumerate(args.backend_urls)
            ),
            max_queued_requests=queue_size,
            max_queued_work=1e10,
            max_queue_residence=queue_deadline,
            backend_timeout=args.client_timeout,
            alpha=1.0,
            beta=FROZEN_BETA,
            routing_mode="round_robin",
        ),
        token_counter=HuggingFaceTokenCounter(str(args.tokenizer)),
    )
    runner = web.AppRunner(create_app(gateway))
    port = 0
    records: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    execution_error: str | None = None
    sampler: asyncio.Task[None] | None = None
    request_tasks: list[asyncio.Task[None]] = []
    accounting: dict[str, Any] = {}
    events: dict[str, list[dict[str, Any]]] = {}
    start = time.perf_counter()
    stop_sampling = asyncio.Event()
    timeout = aiohttp.ClientTimeout(total=args.client_timeout)

    try:
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", args.gateway_port)
        await site.start()
        server = site._server
        if server is None or not server.sockets:
            raise RuntimeError("gateway failed to bind")
        port = server.sockets[0].getsockname()[1]
        async with aiohttp.ClientSession(
            timeout=timeout, connector=aiohttp.TCPConnector(limit=0)
        ) as session:
            before = {url: await fetch_metrics(session, url) for url in args.backend_urls}

            async def sample() -> None:
                while not stop_sampling.is_set():
                    accounting = await gateway.reconcile()
                    row: dict[str, Any] = {
                        "offset_s": time.perf_counter() - start,
                        "queue": accounting["queue"],
                    }
                    for url in args.backend_urls:
                        metrics = await fetch_metrics(session, url)
                        row[url] = {
                            "running": metric_sum(metrics, "vllm:num_requests_running"),
                            "waiting": metric_sum(metrics, "vllm:num_requests_waiting"),
                        }
                    samples.append(row)
                    await asyncio.sleep(0.25)

            sampler = asyncio.create_task(sample())

            async def one(index: int) -> None:
                item = trace[index]
                scheduled = start + item["scheduled_offset_s"]
                actual = time.perf_counter()
                first: float | None = None
                completed = actual
                status = 0
                usage = None
                finish_reason = None
                error_code = None
                error = ""
                saw_done = False
                saw_sse_error = False
                cancelled = False
                try:
                    await asyncio.sleep(max(0.0, scheduled - time.perf_counter()))
                    actual = time.perf_counter()
                    async with session.post(
                        f"http://127.0.0.1:{port}/v1/completions",
                        json={
                            "model": MODEL,
                            "prompt": prompts[index],
                            "max_tokens": 128,
                            "temperature": 0,
                            "stream": True,
                            "stream_options": {"include_usage": True},
                            "ignore_eos": True,
                            "seed": 42,
                        },
                        headers={"X-Request-Id": item["request_id"]},
                    ) as response:
                        status = response.status
                        if status != 200:
                            payload = await response.json()
                            error_code = payload.get("error", {}).get("code")
                        else:
                            buffer = ""
                            async for chunk in response.content.iter_any():
                                buffer += chunk.decode(errors="replace")
                                while "\n" in buffer:
                                    line, buffer = buffer.split("\n", 1)
                                    if line.strip() == "event: error":
                                        saw_sse_error = True
                                        continue
                                    if not line.startswith("data:"):
                                        continue
                                    data = line[5:].strip()
                                    if data == "[DONE]":
                                        saw_done = True
                                        continue
                                    if not data:
                                        continue
                                    event = json.loads(data)
                                    if event.get("error") is not None:
                                        saw_sse_error = True
                                    usage = event.get("usage") or usage
                                    choices = event.get("choices") or []
                                    if choices and choices[0].get("text") and first is None:
                                        first = time.perf_counter()
                                    if choices and choices[0].get("finish_reason") is not None:
                                        finish_reason = choices[0]["finish_reason"]
                        completed = time.perf_counter()
                except asyncio.CancelledError:
                    cancelled = True
                    completed = time.perf_counter()
                    error = "CancelledError: arm stopped before request completed"
                except Exception as exc:
                    completed = time.perf_counter()
                    error = f"{type(exc).__name__}: {exc}"
                success = clean_response_success(
                    status=status,
                    error=error,
                    saw_sse_error=saw_sse_error,
                    saw_done=saw_done,
                    saw_first_content=first is not None,
                    usage=usage,
                    finish_reason=finish_reason,
                )
                envelope = gateway.records.get(item["request_id"])
                queue_residence = None
                if envelope is not None:
                    queue_residence = envelope.timing.measurements().get("queue_residence")
                row = {
                    **item,
                    "actual_offset_s": actual - start,
                    "arrival_lag_s": actual - scheduled,
                    "status": status,
                    "error_code": error_code,
                    "error": error,
                    "usage": usage,
                    "finish_reason": finish_reason,
                    "success": success,
                    "ttft_s": first - actual if first else None,
                    "tpot_s": (completed - first) / 127 if first else None,
                    "e2e_s": completed - actual,
                    "queue_residence_s": queue_residence,
                    "saw_done": saw_done,
                    "saw_sse_error": saw_sse_error,
                }
                row["outcome"] = classify(status, error_code, success)
                row["slo_success"] = is_slo_success(row)
                records.append(row)
                if cancelled:
                    raise asyncio.CancelledError

            request_tasks = [asyncio.create_task(one(index)) for index in range(args.count)]
            await asyncio.wait_for(asyncio.gather(*request_tasks), timeout=args.arm_timeout)
            stop_sampling.set()
            await sampler
            after = {url: await fetch_metrics(session, url) for url in args.backend_urls}
    except Exception as exc:
        execution_error = f"{type(exc).__name__}: {exc}"
    finally:
        stop_sampling.set()
        for task in request_tasks:
            if not task.done():
                task.cancel()
        if request_tasks:
            await asyncio.gather(*request_tasks, return_exceptions=True)
        if sampler is not None:
            if not sampler.done():
                sampler.cancel()
            sampler_result = await asyncio.gather(sampler, return_exceptions=True)
            sampler_error = sampler_result[0]
            if isinstance(sampler_error, Exception) and not isinstance(
                sampler_error, asyncio.CancelledError
            ):
                detail = f"sampler {type(sampler_error).__name__}: {sampler_error}"
                execution_error = f"{execution_error}; {detail}" if execution_error else detail
        try:
            accounting = await gateway.reconcile()
            events = {key: envelope.routing_events for key, envelope in gateway.records.items()}
        except Exception as exc:
            detail = f"reconcile {type(exc).__name__}: {exc}"
            execution_error = f"{execution_error}; {detail}" if execution_error else detail
        try:
            await runner.cleanup()
        except Exception as exc:
            detail = f"cleanup {type(exc).__name__}: {exc}"
            execution_error = f"{execution_error}; {detail}" if execution_error else detail
    ordered = sorted(records, key=lambda row: row["request_id"])
    successes = [row for row in ordered if row["success"]]
    queue_residences = [
        row["queue_residence_s"] for row in ordered if row["queue_residence_s"] is not None
    ]
    reservations = [
        event
        for request_events in events.values()
        for event in request_events
        if event["event"] == "reservation"
    ]
    measurement_duration = args.count / args.rate
    outcomes = {
        name: sum(row["outcome"] == name for row in ordered)
        for name in ("success", "overload_rejection", "queue_expiration", "unexpected_failure")
    }
    metric_deltas = {
        url: {
            name: metric_sum(after[url], name) - metric_sum(before[url], name)
            for name in ("vllm:num_preemptions_total", "vllm:prefix_cache_hits_total")
        }
        for url in args.backend_urls
        if url in before and url in after
    }
    if set(metric_deltas) != set(args.backend_urls):
        execution_error = execution_error or "baseline/final backend metrics are incomplete"
    invalid = validity_reasons(
        configuration=args.configuration,
        records=ordered,
        events=events,
        accounting=accounting,
        metric_deltas=metric_deltas,
        expected_count=args.count,
        execution_error=execution_error,
    )
    offsets = sorted(row["actual_offset_s"] for row in ordered)
    summary = {
        "arm_id": arm_id,
        "configuration": args.configuration,
        "routing_mode": "round_robin",
        "model": MODEL,
        "model_revision": MODEL_REVISION,
        "frozen_beta": FROZEN_BETA,
        "request_cap_per_replica": request_cap,
        "queue_size": queue_size,
        "queue_deadline_s": queue_deadline,
        "offered_count": args.count,
        "configured_offered_rate_rps": args.rate,
        "measurement_duration_s": measurement_duration,
        "trace_sha256": trace_sha256(trace),
        "achieved_arrival_rate_rps": (
            (len(offsets) - 1) / (offsets[-1] - offsets[0]) if len(offsets) > 1 else None
        ),
        "arrival_lag_p50_s": percentile([row["arrival_lag_s"] for row in ordered], 0.5),
        "arrival_lag_p95_s": percentile([row["arrival_lag_s"] for row in ordered], 0.95),
        **{f"{name}_count": value for name, value in outcomes.items()},
        "slo_success_count": sum(row["slo_success"] for row in ordered),
        "slo_goodput_rps": sum(row["slo_success"] for row in ordered) / measurement_duration,
        "slo_fraction_of_offered": sum(row["slo_success"] for row in ordered) / args.count,
        "successful_latency_s": {
            metric: {
                "p50": percentile([row[metric] for row in successes], 0.5),
                "p95": percentile([row[metric] for row in successes], 0.95),
            }
            for metric in ("ttft_s", "tpot_s", "e2e_s")
        },
        "queue_residence_s": {
            "p50": percentile(queue_residences, 0.5),
            "p95": percentile(queue_residences, 0.95),
        },
        "peak_queue_depth": accounting["queue"]["peak_queued_requests"],
        "max_backend_waiting": max(
            (
                sample[url]["waiting"]
                for sample in samples
                for url in args.backend_urls
                if url in sample
            ),
            default=None,
        ),
        "dispatched_counts": {
            replica: sum(event["selected_replica"] == replica for event in reservations)
            for replica in ("replica1", "replica2")
        },
        "metric_deltas": metric_deltas,
        "final_accounting": accounting,
        "infrastructure_valid": not invalid,
        "invalid_reasons": invalid,
        "execution_error": execution_error,
        "percentile_aggregation": "within-arm empirical percentiles over successful requests",
    }
    for name, payload in (
        ("requests.json", ordered),
        ("gateway-events.json", events),
        ("backend-samples.json", samples),
        ("summary.json", summary),
    ):
        (arm_dir / name).write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("pilot", "measured"), required=True)
    parser.add_argument("--configuration", choices=("A", "B", "C"), required=True)
    parser.add_argument("--rate", type=float, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--request-cap", type=int, required=True)
    parser.add_argument("--queue-size", type=int, required=True)
    parser.add_argument("--queue-deadline", type=float, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--backend-urls", nargs=2, default=("http://127.0.0.1:8101", "http://127.0.0.1:8102"))
    parser.add_argument("--gateway-port", type=int, default=8080)
    parser.add_argument("--client-timeout", type=float, default=30.0)
    parser.add_argument("--arm-timeout", type=float, default=180.0)
    args = parser.parse_args()
    allowed = PILOT_SEEDS if args.stage == "pilot" else MEASURED_SEEDS
    expected_count = 64 if args.stage == "pilot" else 256
    if args.seed not in allowed or args.count != expected_count:
        parser.error("stage requires its reserved seed and exact request count")
    if args.client_timeout <= args.queue_deadline:
        parser.error("client timeout must exceed queue deadline")
    if min(args.request_cap, args.queue_size) < 0 or args.queue_deadline <= 0:
        parser.error("limits must be nonnegative and deadline positive")
    return args


if __name__ == "__main__":
    result = asyncio.run(run(parse_args()))
    raise SystemExit(0 if result["infrastructure_valid"] else 1)
