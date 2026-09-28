#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
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

from e8.metrics import classify, evaluation_reasons, percentile, summarize  # noqa: E402
from e8.trace import generate_trace, trace_sha256  # noqa: E402

MODEL = "Qwen/Qwen3-4B-Instruct-2507"
MODEL_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
FROZEN_BETA = 107.2820167
COUNT_THRESHOLD = 8


def generate_prompt(tokenizer: Any, length: int, seed: int) -> str:
    rng = np.random.default_rng(seed)
    forbidden = set(tokenizer.all_special_ids)
    allowed = np.array([token for token in range(tokenizer.vocab_size) if token not in forbidden])
    tokens = allowed[rng.integers(0, len(allowed), size=length)].tolist()
    for _ in range(20):
        prompt = tokenizer.decode(tokens, skip_special_tokens=False)
        encoded = tokenizer.encode(prompt, add_special_tokens=False)
        if len(encoded) == length:
            return prompt
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


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    arm_id = f"e8-{args.stage}-{args.configuration}-r{args.rate:g}-s{args.seed}"
    arm_dir = args.output_root / arm_id
    arm_dir.mkdir(parents=True, exist_ok=False)
    trace = generate_trace(count=args.count, rate=args.rate, seed=args.seed, stage=args.stage)
    (arm_dir / "trace.json").write_text(json.dumps(trace, indent=2) + "\n")
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
    prompts = [generate_prompt(tokenizer, 1024, item["prompt_seed"]) for item in trace]
    if any(len(tokenizer.encode(prompt, add_special_tokens=False)) != 1024 for prompt in prompts):
        raise RuntimeError("prompt token verification failed")

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
    backend_before: dict[str, str] = {}
    backend_after: dict[str, str] = {}
    backend_samples: list[dict[str, Any]] = []
    execution_error: str | None = None
    start = 0.0
    request_tasks: list[asyncio.Task[None]] = []
    sampler: asyncio.Task[None] | None = None
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

            async def sample_backends() -> None:
                while not stop_sampling.is_set():
                    sample: dict[str, Any] = {"offset_s": time.perf_counter() - start}
                    for url in args.backend_urls:
                        metrics = await fetch_metrics(session, url)
                        sample[url] = {
                            "running": metric_sum(metrics, "vllm:num_requests_running"),
                            "waiting": metric_sum(metrics, "vllm:num_requests_waiting"),
                        }
                    backend_samples.append(sample)
                    await asyncio.sleep(0.1)

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
                events = gateway.records.get(item["request_id"])
                route = events.routing_events if events else []
                decision = decision_event(route)
                ttft = first - actual if first is not None else None
                e2e = completed - actual if success else None
                tpot = ((completed - first) / 127) if success and first is not None else None
                outcome = classify(status, error_code, success, timed_out)
                rows.append(
                    {
                        **item,
                        "configuration": args.configuration,
                        "status": status,
                        "error_code": error_code,
                        "error": error,
                        "outcome": outcome,
                        "actual_offset_s": actual - start,
                        "arrival_lag_s": actual - scheduled,
                        "completed_offset_s": completed - start,
                        "ttft_s": ttft,
                        "tpot_s": tpot,
                        "e2e_s": e2e,
                        "admission_decision": decision.get("predicted_reject"),
                        "decision_reason": decision.get("decision_reason"),
                        "selected_replica": decision.get("selected_replica"),
                        "decision_event": decision,
                        "routing_events": route,
                    }
                )

            # The offer clock starts only after setup, tokenization, and baseline collection.
            start = time.perf_counter()
            sampler = asyncio.create_task(sample_backends())
            request_tasks = [asyncio.create_task(one(index)) for index in range(len(trace))]
            async with asyncio.timeout(args.arm_timeout):
                await asyncio.gather(*request_tasks)
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
        rows.sort(key=lambda row: row["request_id"])
        (arm_dir / "requests.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        )
        summary = summarize(rows, offered_count=args.count, rate=args.rate)
        reasons = evaluation_reasons(
            rows, expected_count=args.count, configuration=args.configuration
        )
        if execution_error:
            reasons.append(execution_error)
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
        elif any(value != 0 for values in metric_deltas.values() for value in values.values()):
            reasons.append("preemption or prefix-cache hit occurred")
        arrival_lags = [float(row["arrival_lag_s"]) for row in rows]
        arrival_lag_p95 = percentile(arrival_lags, 0.95)
        if arrival_lag_p95 is not None and arrival_lag_p95 > 0.050:
            reasons.append("arrival lag p95 exceeded 50 ms")
        if arrival_lags and max(arrival_lags) > 0.250:
            reasons.append("single arrival lag exceeded 250 ms")
        if any(
            replica["active_reservations"] or replica["inflight"] or replica["outstanding_work"]
            for replica in accounting["replicas"].values()
        ):
            reasons.append("final reservation accounting is nonzero")
        manifest = {
            "arm_id": arm_id,
            "stage": args.stage,
            "configuration": args.configuration,
            "rate": args.rate,
            "count": args.count,
            "seed": args.seed,
            "trace_sha256": trace_sha256(trace),
            "model": MODEL,
            "model_revision": MODEL_REVISION,
            "input_tokens": 1024,
            "output_tokens": 128,
            "beta": FROZEN_BETA,
            "count_threshold": COUNT_THRESHOLD,
            "routing": "round_robin",
            "queue_enabled": False,
            "evaluation_valid": not reasons,
            "evaluation_reasons": reasons,
            "summary": summary,
            "arrival_lag_p95_s": arrival_lag_p95,
            "arrival_lag_max_s": max(arrival_lags) if arrival_lags else None,
            "max_backend_waiting": max(
                (
                    sample[url]["waiting"]
                    for sample in backend_samples
                    for url in args.backend_urls
                    if url in sample
                ),
                default=None,
            ),
            "backend_metric_deltas": metric_deltas,
            "final_accounting": accounting,
            "backend_metrics_before": backend_before,
            "backend_metrics_after": backend_after,
        }
        (arm_dir / "backend-samples.json").write_text(
            json.dumps(backend_samples, indent=2, sort_keys=True) + "\n"
        )
        (arm_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configuration", choices=("A", "B"), required=True)
    parser.add_argument("--stage", choices=("smoke", "pilot", "measured"), required=True)
    parser.add_argument("--rate", type=float, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--backend-url", dest="backend_urls", action="append", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--client-timeout", type=float, default=60.0)
    parser.add_argument("--arm-timeout", type=float, default=180.0)
    args = parser.parse_args()
    if len(args.backend_urls) != 2:
        parser.error("exactly two --backend-url values are required")
    return args


if __name__ == "__main__":
    result = asyncio.run(execute(parse_args()))
    print(json.dumps({"arm_id": result["arm_id"], "evaluation_valid": result["evaluation_valid"]}))
    raise SystemExit(0 if result["evaluation_valid"] else 2)
