#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
import numpy as np
from aiohttp import web
from transformers import AutoTokenizer

GATEWAY_ROOT = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(GATEWAY_ROOT))

from gateway.app import Gateway, create_app  # noqa: E402
from gateway.config import GatewayConfig, ReplicaConfig  # noqa: E402
from gateway.prediction import CountAdmissionPolicy  # noqa: E402
from gateway.tokenizers import HuggingFaceTokenCounter  # noqa: E402

from e9.metrics import (  # noqa: E402
    OFFER_WINDOW_S,
    classify,
    evaluation_reasons,
    percentile,
    recovery_summary,
    summarize,
)
from e9.trace import content_sha256, generate_trace, scheduled_trace_sha256  # noqa: E402

MODEL = "Qwen/Qwen3-4B-Instruct-2507"
MODEL_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
FROZEN_BETA = 107.2820167
COUNT_THRESHOLD = 8
SAMPLE_INTERVAL_S = 0.05


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def generate_prompt(tokenizer: Any, length: int, seed: int) -> tuple[str, list[int]]:
    rng = np.random.default_rng(seed)
    forbidden = set(tokenizer.all_special_ids)
    allowed = np.array([token for token in range(tokenizer.vocab_size) if token not in forbidden])
    tokens = allowed[rng.integers(0, len(allowed), size=length)].tolist()
    for _ in range(20):
        prompt = tokenizer.decode(tokens, skip_special_tokens=False)
        encoded = tokenizer.encode(prompt, add_special_tokens=False)
        if len(encoded) == length:
            return prompt, encoded
        tokens = encoded[:length]
        if len(tokens) < length:
            tokens += allowed[rng.integers(0, len(allowed), size=length - len(tokens))].tolist()
    raise RuntimeError("unable to generate exact-token prompt")


def decision_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [event for event in events if event["event"] == "prediction_admission_decision"]
    return matches[0] if len(matches) == 1 else {}


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


def compact_accounting(accounting: dict[str, Any]) -> dict[str, Any]:
    replicas = accounting["replicas"]
    return {
        "outstanding_requests": sum(int(replica["inflight"]) for replica in replicas.values()),
        "outstanding_work": sum(
            float(replica["outstanding_work"]) for replica in replicas.values()
        ),
        "queue_depth": int(accounting["queue"]["queued_requests"]),
        "queue_work": float(accounting["queue"]["queued_work"]),
        "replicas": {
            name: {
                "inflight": int(replica["inflight"]),
                "outstanding_work": float(replica["outstanding_work"]),
                "active_reservations": int(replica["active_reservations"]),
            }
            for name, replica in replicas.items()
        },
    }


def lifecycle_reasons(rows: list[dict[str, Any]]) -> list[str]:
    reasons = []
    for row in rows:
        events = row.get("routing_events", [])
        names = [event.get("event") for event in events]
        decision = row.get("decision_event", {})
        if row["outcome"] == "prediction_rejected":
            if decision.get("observed_count", -1) < COUNT_THRESHOLD:
                reasons.append(f"{row['request_id']}: rejection below threshold")
            if {"reservation", "backend_dispatch"} & set(names):
                reasons.append(f"{row['request_id']}: rejected request dispatched or reserved")
        elif row["outcome"] == "accepted_success":
            required = (
                "prediction_admission_decision",
                "reservation",
                "backend_dispatch",
                "final_release",
            )
            if any(name not in names for name in required):
                reasons.append(f"{row['request_id']}: accepted lifecycle incomplete")
            elif not all(
                names.index(a) < names.index(b)
                for a, b in zip(required, required[1:], strict=False)
            ):
                reasons.append(f"{row['request_id']}: decision/dispatch lifecycle out of order")
            if (
                decision.get("enforcement_enabled")
                and decision.get("observed_count", 0) >= COUNT_THRESHOLD
            ):
                reasons.append(f"{row['request_id']}: enforced admission at or above threshold")
    return reasons


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    arm_id = f"e9-{args.stage}-{args.traffic}-{args.configuration}-s{args.seed}"
    arm_dir = args.output_root / arm_id
    arm_dir.mkdir(parents=True, exist_ok=False)
    trace = generate_trace(count=args.count, seed=args.seed, stage=args.stage, traffic=args.traffic)
    (arm_dir / "trace.json").write_text(json.dumps(trace, indent=2) + "\n")
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
    prompt_records = []
    prompts = []
    for item in trace:
        prompt, token_ids = generate_prompt(tokenizer, 1024, item["prompt_seed"])
        prompts.append(prompt)
        prompt_records.append(
            {
                "request_id": item["request_id"],
                "prompt_seed": item["prompt_seed"],
                "prompt": prompt,
                "input_token_ids": token_ids,
            }
        )
    (arm_dir / "prompts.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in prompt_records)
    )

    mode = "shadow" if args.configuration == "A" else "enforce"
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
            backend_retry_limit=0,
            alpha=1.0,
            beta=FROZEN_BETA,
            routing_mode="round_robin",
            prediction_admission_mode=mode,
            prediction_admission_count_threshold=COUNT_THRESHOLD,
        ),
        token_counter=HuggingFaceTokenCounter(str(args.tokenizer)),
        prediction_admission_policy=CountAdmissionPolicy(COUNT_THRESHOLD),
    )
    runner = web.AppRunner(create_app(gateway))
    rows: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    backend_before: dict[str, str] = {}
    backend_after: dict[str, str] = {}
    execution_error: str | None = None
    request_tasks: list[asyncio.Task[None]] = []
    sampler: asyncio.Task[None] | None = None
    start = 0.0
    arm_start_utc: str | None = None
    stop_sampling = asyncio.Event()
    try:
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        server = site._server
        if server is None or not server.sockets:
            raise RuntimeError("gateway failed to bind")
        port = server.sockets[0].getsockname()[1]
        timeout = aiohttp.ClientTimeout(total=args.client_timeout)
        async with aiohttp.ClientSession(
            timeout=timeout, connector=aiohttp.TCPConnector(limit=0)
        ) as session:
            backend_before = {url: await fetch_metrics(session, url) for url in args.backend_urls}

            async def sample_once() -> None:
                accounting = compact_accounting(await gateway.reconcile())
                sample: dict[str, Any] = {
                    "offset_s": time.perf_counter() - start,
                    "gateway": accounting,
                    "backends": {},
                }
                for url in args.backend_urls:
                    metrics = await fetch_metrics(session, url)
                    sample["backends"][url] = {
                        "running": metric_sum(metrics, "vllm:num_requests_running"),
                        "waiting": metric_sum(metrics, "vllm:num_requests_waiting"),
                    }
                samples.append(sample)

            async def sample_loop() -> None:
                while not stop_sampling.is_set():
                    try:
                        await sample_once()
                    except Exception as exc:
                        samples.append(
                            {
                                "offset_s": time.perf_counter() - start,
                                "sample_error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                    await asyncio.sleep(SAMPLE_INTERVAL_S)

            async def one(index: int) -> None:
                item = trace[index]
                scheduled = start + item["scheduled_offset_s"]
                await asyncio.sleep(max(0.0, scheduled - time.perf_counter()))
                actual = time.perf_counter()
                first: float | None = None
                status = 0
                error_code: str | None = None
                error = ""
                usage: dict[str, Any] | None = None
                finish_reason: str | None = None
                saw_done = False
                stream_chunk_count = 0
                timed_out = False
                try:
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
                            body = await response.json()
                            error_code = body.get("error", {}).get("code")
                        else:
                            async for raw in response.content:
                                line = raw.decode(errors="replace").strip()
                                if not line.startswith("data:"):
                                    continue
                                data = line[5:].strip()
                                if data == "[DONE]":
                                    saw_done = True
                                elif data:
                                    stream_chunk_count += 1
                                    event = json.loads(data)
                                    usage = event.get("usage") or usage
                                    choices = event.get("choices") or []
                                    if choices and choices[0].get("text") and first is None:
                                        first = time.perf_counter()
                                    if choices and choices[0].get("finish_reason") is not None:
                                        finish_reason = choices[0]["finish_reason"]
                except TimeoutError as exc:
                    timed_out = True
                    error = f"{type(exc).__name__}: {exc}"
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                completed = time.perf_counter()
                completion_tokens = int((usage or {}).get("completion_tokens", 0))
                success = bool(
                    status == 200
                    and not error
                    and saw_done
                    and first is not None
                    and finish_reason == "length"
                    and completion_tokens == 128
                )
                record = gateway.records.get(item["request_id"])
                route = record.routing_events if record else []
                decision = decision_event(route)
                rows.append(
                    {
                        **item,
                        "configuration": args.configuration,
                        "status": status,
                        "error_code": error_code,
                        "error": error,
                        "outcome": classify(status, error_code, success, timed_out),
                        "actual_offset_s": actual - start,
                        "arrival_lag_s": actual - scheduled,
                        "first_content_offset_s": first - start if first is not None else None,
                        "completed_offset_s": completed - start,
                        "ttft_s": first - actual if first is not None else None,
                        "tpot_s": (
                            (completed - first) / (completion_tokens - 1)
                            if success and first is not None and completion_tokens > 1
                            else None
                        ),
                        "e2e_s": completed - actual if success else None,
                        "usage": usage,
                        "finish_reason": finish_reason,
                        "saw_stream_done": saw_done,
                        "stream_chunk_count": stream_chunk_count,
                        "admission_decision": decision.get("predicted_reject"),
                        "decision_reason": decision.get("decision_reason"),
                        "selected_replica": decision.get("selected_replica"),
                        "decision_event": decision,
                        "routing_events": route,
                    }
                )

            arm_start_utc = utc_now()
            start = time.perf_counter()
            sampler = asyncio.create_task(sample_loop())
            request_tasks = [asyncio.create_task(one(index)) for index in range(len(trace))]
            request_group = asyncio.gather(*request_tasks)
            offer_window = asyncio.sleep(max(0.0, start + OFFER_WINDOW_S - time.perf_counter()))
            async with asyncio.timeout(args.arm_timeout):
                await asyncio.gather(request_group, offer_window)
            await sample_once()
            stop_sampling.set()
            await sampler
            backend_after = {url: await fetch_metrics(session, url) for url in args.backend_urls}
    except Exception as exc:
        execution_error = f"{type(exc).__name__}: {exc}"
        for task in request_tasks:
            task.cancel()
        if request_tasks:
            await asyncio.gather(*request_tasks, return_exceptions=True)
    finally:
        stop_sampling.set()
        if sampler is not None and not sampler.done():
            sampler.cancel()
            await asyncio.gather(sampler, return_exceptions=True)
        accounting = await gateway.reconcile()
        await runner.cleanup()
        arm_end_utc = utc_now()
        monotonic_duration = time.perf_counter() - start if start else 0.0
        rows.sort(key=lambda row: row["request_id"])
        (arm_dir / "requests.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        )
        (arm_dir / "state-samples.json").write_text(
            json.dumps(samples, indent=2, sort_keys=True) + "\n"
        )
        summary = summarize(rows, offered_count=args.count)
        reasons = evaluation_reasons(
            rows, expected_count=args.count, configuration=args.configuration
        )
        reasons.extend(lifecycle_reasons(rows))
        if execution_error:
            reasons.append(execution_error)
        if any("sample_error" in sample for sample in samples):
            reasons.append("state sampling was incomplete")
        metric_names = ("vllm:num_preemptions_total", "vllm:prefix_cache_hits_total")
        metric_deltas = {
            url: {
                name: metric_sum(backend_after[url], name) - metric_sum(backend_before[url], name)
                for name in metric_names
            }
            for url in args.backend_urls
            if url in backend_before and url in backend_after
        }
        if set(metric_deltas) != set(args.backend_urls):
            reasons.append("baseline/final backend metrics are incomplete")
        elif any(
            not math.isfinite(value)
            for values in metric_deltas.values()
            for value in values.values()
        ):
            reasons.append("backend metric delta is nonfinite")
        elif any(value != 0 for values in metric_deltas.values() for value in values.values()):
            reasons.append("preemption or prefix-cache hit occurred")
        arrival_lags = [float(row["arrival_lag_s"]) for row in rows]
        arrival_p95 = percentile(arrival_lags, 0.95)
        if arrival_p95 is not None and arrival_p95 > 0.050:
            reasons.append("arrival lag p95 exceeded 50 ms")
        if arrival_lags and max(arrival_lags) > 0.250:
            reasons.append("single arrival lag exceeded 250 ms")
        if any(
            replica["active_reservations"] or replica["inflight"] or replica["outstanding_work"]
            for replica in accounting["replicas"].values()
        ):
            reasons.append("final reservation accounting is nonzero")
        if accounting["queue"]["queued_requests"] or accounting["queue"]["queued_work"]:
            reasons.append("final gateway queue accounting is nonzero")
        complete_samples = [sample for sample in samples if "gateway" in sample]
        max_backend_waiting = max(
            (
                backend["waiting"]
                for sample in complete_samples
                for backend in sample["backends"].values()
            ),
            default=None,
        )
        backlog = recovery_summary(complete_samples)
        backlog["peaks"] = {
            "gateway_queue_depth": max(
                (sample["gateway"]["queue_depth"] for sample in complete_samples), default=None
            ),
            "gateway_outstanding_requests": max(
                (sample["gateway"]["outstanding_requests"] for sample in complete_samples),
                default=None,
            ),
            "gateway_outstanding_work": max(
                (sample["gateway"]["outstanding_work"] for sample in complete_samples),
                default=None,
            ),
            "backend_running": max(
                (
                    backend["running"]
                    for sample in complete_samples
                    for backend in sample["backends"].values()
                ),
                default=None,
            ),
            "backend_waiting": max_backend_waiting,
        }
        manifest = {
            "arm_id": arm_id,
            "stage": args.stage,
            "traffic": args.traffic,
            "configuration": args.configuration,
            "count": args.count,
            "seed": args.seed,
            "arm_order_index": args.arm_order_index,
            "catalog_sha256": args.catalog_sha256,
            "content_sha256": content_sha256(trace),
            "scheduled_trace_sha256": scheduled_trace_sha256(trace),
            "offer_window_s": OFFER_WINDOW_S,
            "arm_start_utc": arm_start_utc,
            "arm_end_utc": arm_end_utc,
            "monotonic_duration_s": monotonic_duration,
            "model": MODEL,
            "model_revision": MODEL_REVISION,
            "input_tokens": 1024,
            "output_tokens": 128,
            "beta": FROZEN_BETA,
            "count_threshold": COUNT_THRESHOLD,
            "routing": "round_robin",
            "queue_enabled": False,
            "prefix_cache_enabled": False,
            "backend_retry_limit": 0,
            "replica_count": 2,
            "safety_limits": {
                "max_inflight_per_replica": 128,
                "max_work_per_replica": 1e9,
                "client_timeout_s": args.client_timeout,
            },
            "evaluation_valid": not reasons,
            "evaluation_reasons": reasons,
            "summary": summary,
            "backlog": backlog,
            "arrival_lag_p95_s": arrival_p95,
            "arrival_lag_max_s": max(arrival_lags) if arrival_lags else None,
            "backend_metric_deltas": metric_deltas,
            "final_accounting": accounting,
            "backend_metrics_before": backend_before,
            "backend_metrics_after": backend_after,
        }
        (arm_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--traffic", choices=("smooth", "bursty"), required=True)
    parser.add_argument("--configuration", choices=("A", "B"), required=True)
    parser.add_argument("--stage", choices=("smoke", "pilot", "measured"), required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--arm-order-index", type=int, required=True)
    parser.add_argument("--catalog-sha256", required=True)
    parser.add_argument("--backend-url", dest="backend_urls", action="append", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--client-timeout", type=float, default=60.0)
    parser.add_argument("--arm-timeout", type=float, default=240.0)
    args = parser.parse_args()
    if len(args.backend_urls) != 2:
        parser.error("exactly two --backend-url values are required")
    return args


if __name__ == "__main__":
    result = asyncio.run(execute(parse_args()))
    print(json.dumps({"arm_id": result["arm_id"], "evaluation_valid": result["evaluation_valid"]}))
    raise SystemExit(0 if result["evaluation_valid"] else 2)
