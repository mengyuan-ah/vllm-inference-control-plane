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
from gateway.prediction import ShadowPredictionConfig, ShadowPredictor
from gateway.tokenizers import HuggingFaceTokenCounter
from transformers import AutoTokenizer

from e7.metrics import (
    clean_response_success,
    confusion,
    label_outcome,
    percentile,
    prediction_capture_reasons,
    prediction_evaluation_reasons,
    validity_reasons,
)
from e7.trace import CALIBRATION_SEEDS, MEASURED_SEEDS, SMOKE_SEEDS, generate_trace, trace_sha256

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
        tokens = (
            encoded[:length]
            if len(encoded) > length
            else encoded
            + allowed[rng.integers(0, len(allowed), size=length - len(encoded))].tolist()
        )
    raise RuntimeError("unable to generate exact-token prompt")


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
    arm_id = f"e7-{args.stage}-r{args.rate:g}-s{args.seed}"
    arm_dir = args.output_root / arm_id
    arm_dir.mkdir(parents=True, exist_ok=False)
    trace = generate_trace(count=args.count, rate=args.rate, seed=args.seed, stage=args.stage)
    (arm_dir / "trace.json").write_text(json.dumps(trace, indent=2) + "\n")
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
    prompts = [generate_prompt(tokenizer, 1024, row["prompt_seed"]) for row in trace]
    if any(len(tokenizer.encode(prompt, add_special_tokens=False)) != 1024 for prompt in prompts):
        raise RuntimeError("prompt token verification failed")

    parameters = (
        json.loads(args.predictor_parameters.read_text()) if args.predictor_parameters else {}
    )
    predictor = ShadowPredictor(
        ShadowPredictionConfig(
            version=parameters.get("predictor_version", "e7-calibration-capture-v1"),
            count_threshold=parameters.get("count_threshold"),
            work_threshold=parameters.get("work_threshold"),
        )
    )
    gateway = Gateway(
        GatewayConfig(
            replicas=tuple(
                ReplicaConfig(
                    f"replica{index + 1}",
                    url,
                    max_inflight=128,
                    max_work=1e9,
                    recovery_max_inflight=128,
                    recovery_max_work=1e9,
                )
                for index, url in enumerate(args.backend_urls)
            ),
            max_queued_requests=0,
            max_queued_work=1e10,
            max_queue_residence=0.5,
            backend_timeout=args.client_timeout,
            alpha=1.0,
            beta=FROZEN_BETA,
            routing_mode="round_robin",
        ),
        token_counter=HuggingFaceTokenCounter(str(args.tokenizer)),
        shadow_predictor=predictor,
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
                prediction: dict[str, Any] = {}
                if envelope is not None:
                    prediction = next(
                        (
                            event
                            for event in envelope.routing_events
                            if event.get("event") == "shadow_prediction"
                        ),
                        {},
                    )
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
                    "selected_replica": prediction.get("selected_replica"),
                    "prediction_timestamp": prediction.get("prediction_timestamp"),
                    "feature_timestamp": prediction.get("feature_timestamp"),
                    "selected_inflight_before": prediction.get("selected_inflight_before"),
                    "selected_outstanding_work_before": prediction.get(
                        "selected_outstanding_work_before"
                    ),
                    "predictor_version": prediction.get("predictor_version"),
                    "predictor_parameters": prediction.get("parameters"),
                    "predictions": prediction.get("predictions", {}),
                    "prediction_reasons": prediction.get("prediction_reasons", {}),
                    "prediction_computation_ns": prediction.get("computation_ns"),
                    "saw_done": saw_done,
                    "saw_sse_error": saw_sse_error,
                }
                row["outcome"] = "success" if success else "unexpected_failure"
                row.update(label_outcome(row))
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
    reservations = [
        event
        for request_events in events.values()
        for event in request_events
        if event["event"] == "reservation"
    ]
    measurement_duration = args.count / args.rate
    outcomes = {
        name: sum(row["outcome"] == name for row in ordered)
        for name in ("success", "unexpected_failure")
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
    infrastructure_invalid = validity_reasons(
        records=ordered,
        events=events,
        accounting=accounting,
        metric_deltas=metric_deltas,
        expected_count=args.count,
        execution_error=execution_error,
    )
    capture_invalid = prediction_capture_reasons(
        records=ordered,
        events=events,
        expected_count=args.count,
    )
    evaluation_invalid = (
        prediction_evaluation_reasons(
            records=ordered,
            expected_count=args.count,
            predictor_names=("always_pass", "count_threshold", "work_threshold"),
        )
        if args.stage == "measured"
        else []
    )
    offsets = sorted(row["actual_offset_s"] for row in ordered)
    summary = {
        "arm_id": arm_id,
        "stage": args.stage,
        "routing_mode": "round_robin",
        "model": MODEL,
        "model_revision": MODEL_REVISION,
        "frozen_beta": FROZEN_BETA,
        "request_cap_per_replica": 128,
        "queue_size": 0,
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
        "slo_miss_count": sum(row["slo_miss"] is True for row in ordered),
        "ttft_miss_count": sum(row["ttft_miss"] is True for row in ordered),
        "tpot_miss_count": sum(row["tpot_miss"] is True for row in ordered),
        "e2e_miss_count": sum(row["e2e_miss"] is True for row in ordered),
        "successful_latency_s": {
            metric: {
                "p50": percentile([row[metric] for row in successes], 0.5),
                "p95": percentile([row[metric] for row in successes], 0.95),
            }
            for metric in ("ttft_s", "tpot_s", "e2e_s")
        },
        "predictors": {
            name: confusion(ordered, name)
            for name in ("always_pass", "count_threshold", "work_threshold")
        },
        "prediction_computation_ns": {
            "p50": percentile(
                [
                    row["prediction_computation_ns"]
                    for row in ordered
                    if row["prediction_computation_ns"] is not None
                ],
                0.5,
            ),
            "p95": percentile(
                [
                    row["prediction_computation_ns"]
                    for row in ordered
                    if row["prediction_computation_ns"] is not None
                ],
                0.95,
            ),
        },
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
        "infrastructure_valid": not infrastructure_invalid,
        "invalid_reasons": infrastructure_invalid,
        "prediction_capture_complete": not capture_invalid,
        "prediction_capture_reasons": capture_invalid,
        "prediction_evaluation_complete": not evaluation_invalid,
        "prediction_evaluation_reasons": evaluation_invalid,
        "arm_valid": not infrastructure_invalid and not capture_invalid and not evaluation_invalid,
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
    parser.add_argument("--stage", choices=("smoke", "calibration", "measured"), required=True)
    parser.add_argument("--rate", type=float, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--predictor-parameters", type=Path)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--backend-urls", nargs=2, default=("http://127.0.0.1:8101", "http://127.0.0.1:8102")
    )
    parser.add_argument("--gateway-port", type=int, default=8080)
    parser.add_argument("--client-timeout", type=float, default=30.0)
    parser.add_argument("--arm-timeout", type=float, default=180.0)
    args = parser.parse_args()
    allowed = {"smoke": SMOKE_SEEDS, "calibration": CALIBRATION_SEEDS, "measured": MEASURED_SEEDS}[
        args.stage
    ]
    expected_count = {"smoke": 8, "calibration": 96, "measured": 256}[args.stage]
    if args.seed not in allowed or args.count != expected_count:
        parser.error("stage requires its reserved seed and exact request count")
    if args.stage == "measured" and args.predictor_parameters is None:
        parser.error("measured stage requires frozen predictor parameters")
    return args


if __name__ == "__main__":
    result = asyncio.run(run(parse_args()))
    raise SystemExit(0 if result["arm_valid"] else 1)
