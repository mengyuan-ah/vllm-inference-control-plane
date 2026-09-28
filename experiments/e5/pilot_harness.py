#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from pathlib import Path
from typing import Any

import aiohttp
import numpy as np
from aiohttp import web
from gateway.app import Gateway, create_app
from gateway.config import GatewayConfig, ReplicaConfig
from gateway.tokenizers import HuggingFaceTokenCounter
from gateway.tracker import WORK_ZERO_TOLERANCE
from transformers import AutoTokenizer

from e5.trace import FROZEN_BETA, MEASURED_SEEDS, PILOT_SEEDS, generate_trace, trace_sha256

MODEL = "Qwen/Qwen3-4B-Instruct-2507"


def validate_final_accounting(accounting: dict[str, Any]) -> list[str]:
    reasons = []
    queue = accounting["queue"]
    queued_work = float(queue["queued_work"])
    if queue["queued_requests"] != 0:
        reasons.append("gateway queue count did not drain to exact zero")
    if not math.isfinite(queued_work) or abs(queued_work) > WORK_ZERO_TOLERANCE:
        reasons.append("gateway queued work did not drain within tolerance")
    for replica_id, replica in accounting["replicas"].items():
        work = float(replica["outstanding_work"])
        if replica["inflight"] != 0:
            reasons.append(f"{replica_id} inflight count did not drain to exact zero")
        if replica.get("active_reservations", 0) != 0:
            reasons.append(f"{replica_id} active reservation ledger did not drain")
        if not math.isfinite(work) or abs(work) > WORK_ZERO_TOLERANCE:
            reasons.append(f"{replica_id} outstanding work did not drain within tolerance")
    return reasons


def validate_arm(
    summary: dict[str, Any],
    requests: list[dict[str, Any]],
    events: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    reasons = validate_final_accounting(summary["final_accounting"])
    if summary["successes"] != summary["count"] or any(not row["success"] for row in requests):
        reasons.append("one or more requests failed")
    if summary["queued_events"]:
        reasons.append("gateway queueing was observed")
    if summary["rejections"]:
        reasons.append("gateway rejection was observed")
    if summary["failover_events"]:
        reasons.append("failover was observed")
    if summary["health_transition_events"]:
        reasons.append("health transition was observed")
    if any(
        metrics[name] != 0
        for metrics in summary["metric_deltas"].values()
        for name in ("vllm:num_preemptions_total", "vllm:prefix_cache_hits_total")
    ):
        reasons.append("preemption or prefix-cache hit was observed")
    if set(events) != {row["request_id"] for row in requests}:
        reasons.append("routing evidence request identities are incomplete")
    else:
        for request_id, request_events in events.items():
            sequence = [event["event"] for event in request_events]
            required = ["reservation", "prefill_release", "final_release"]
            release_sequence = [event for event in sequence if event in required]
            if release_sequence != required or any(
                sequence.count(event) != 1 for event in required
            ):
                reasons.append(f"{request_id} has invalid release sequence: {sequence}")
    return {
        "infrastructure_valid": not reasons,
        "invalid_reasons": reasons,
        "backend_waiting_is_measurement": True,
    }


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return (
        ordered[low]
        if low == high
        else ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    )


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
    raise RuntimeError(f"unable to create exact {length}-token prompt for seed {seed}")


async def fetch_metrics(session: aiohttp.ClientSession, backend_url: str) -> str:
    async with session.get(f"{backend_url}/metrics") as response:
        response.raise_for_status()
        return await response.text()


def metric_sum(text: str, name: str) -> float:
    values = []
    for line in text.splitlines():
        if line.startswith(name + "{") or line.startswith(name + " "):
            values.append(float(line.rsplit(" ", 1)[-1]))
    return sum(values)


async def run_arm(args: argparse.Namespace, policy: str) -> dict[str, Any]:
    arm_id = f"{args.stage}-{args.workload}-r{args.rate:g}-{policy}-s{args.seed}"
    arm_dir = args.output_root / arm_id
    arm_dir.mkdir(parents=True, exist_ok=False)
    trace = generate_trace(
        args.workload, request_count=args.count, rate=args.rate, seed=args.seed, stage=args.stage
    )
    trace_digest = trace_sha256(trace)
    (arm_dir / "trace.json").write_text(json.dumps(trace, indent=2) + "\n")
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
    prompts = [
        generate_prompt(tokenizer, item["input_tokens"], item["prompt_seed"]) for item in trace
    ]
    for item, prompt in zip(trace, prompts, strict=True):
        assert len(tokenizer.encode(prompt, add_special_tokens=False)) == item["input_tokens"]

    config = GatewayConfig(
        replicas=(
            ReplicaConfig("replica1", args.backend_urls[0], max_inflight=128, max_work=1e9),
            ReplicaConfig("replica2", args.backend_urls[1], max_inflight=128, max_work=1e9),
        ),
        max_queued_requests=256,
        max_queued_work=1e10,
        max_queue_residence=300,
        backend_timeout=300,
        alpha=1.0,
        beta=FROZEN_BETA,
        routing_mode=policy,
    )
    gateway = Gateway(config, token_counter=HuggingFaceTokenCounter(str(args.tokenizer)))
    runner = web.AppRunner(create_app(gateway))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", args.gateway_port)
    await site.start()
    server = site._server
    if server is None or not server.sockets:
        raise RuntimeError("gateway did not bind a listening socket")
    gateway_port = server.sockets[0].getsockname()[1]
    timeout = aiohttp.ClientTimeout(total=300)
    connector = aiohttp.TCPConnector(limit=0)
    start = time.perf_counter()
    records: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    stop_sampling = asyncio.Event()

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        before = {url: await fetch_metrics(session, url) for url in args.backend_urls}

        async def sample() -> None:
            while not stop_sampling.is_set():
                row: dict[str, Any] = {"offset_s": time.perf_counter() - start}
                for url in args.backend_urls:
                    text = await fetch_metrics(session, url)
                    row[url] = {
                        "running": metric_sum(text, "vllm:num_requests_running"),
                        "waiting": metric_sum(text, "vllm:num_requests_waiting"),
                    }
                samples.append(row)
                await asyncio.sleep(0.25)

        sampler = asyncio.create_task(sample())

        async def one(index: int) -> None:
            item = trace[index]
            scheduled = start + item["scheduled_offset_s"]
            await asyncio.sleep(max(0.0, scheduled - time.perf_counter()))
            actual = time.perf_counter()
            first: float | None = None
            completed = actual
            usage = None
            finish_reason = None
            status = 0
            error = ""
            success = False
            body = {
                "model": MODEL,
                "prompt": prompts[index],
                "max_tokens": item["requested_output_tokens"],
                "temperature": 0,
                "stream": True,
                "stream_options": {"include_usage": True},
                "ignore_eos": True,
                "seed": 42,
            }
            try:
                async with session.post(
                    f"http://127.0.0.1:{gateway_port}/v1/completions",
                    json=body,
                    headers={"X-Request-Id": item["request_id"]},
                ) as response:
                    status = response.status
                    response.raise_for_status()
                    buffer = ""
                    async for chunk in response.content.iter_any():
                        buffer += chunk.decode(errors="replace")
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data or data == "[DONE]":
                                continue
                            event = json.loads(data)
                            if event.get("usage") is not None:
                                usage = event["usage"]
                            choices = event.get("choices") or []
                            if choices:
                                text = choices[0].get("text") or ""
                                if text and first is None:
                                    first = time.perf_counter()
                                if choices[0].get("finish_reason") is not None:
                                    finish_reason = choices[0]["finish_reason"]
                    completed = time.perf_counter()
                success = (
                    first is not None
                    and usage is not None
                    and usage.get("prompt_tokens") == item["input_tokens"]
                    and usage.get("completion_tokens") == item["requested_output_tokens"]
                    and finish_reason == "length"
                )
            except Exception as exc:
                completed = time.perf_counter()
                error = f"{type(exc).__name__}: {exc}"
            records.append(
                {
                    **item,
                    "actual_offset_s": actual - start,
                    "arrival_lag_s": actual - scheduled,
                    "ttft_s": first - actual if first else None,
                    "e2e_s": completed - actual,
                    "tpot_s": (completed - first) / (item["requested_output_tokens"] - 1)
                    if first
                    else None,
                    "status": status,
                    "usage": usage,
                    "finish_reason": finish_reason,
                    "success": success,
                    "error": error,
                }
            )

        await asyncio.gather(*(one(index) for index in range(args.count)))
        stop_sampling.set()
        await sampler
        after = {url: await fetch_metrics(session, url) for url in args.backend_urls}

    accounting = await gateway.reconcile()
    events = {key: value.routing_events for key, value in gateway.records.items()}
    await runner.cleanup()
    ordered = sorted(records, key=lambda row: row["request_id"])
    reservations = [
        event
        for request_events in events.values()
        for event in request_events
        if event["event"] == "reservation"
    ]
    work = {
        replica: sum(
            float(event["reserved_work"])
            for event in reservations
            if event["selected_replica"] == replica
        )
        for replica in ("replica1", "replica2")
    }
    counts = {
        replica: sum(event["selected_replica"] == replica for event in reservations)
        for replica in ("replica1", "replica2")
    }
    metric_deltas = {
        url: {
            name: metric_sum(after[url], name) - metric_sum(before[url], name)
            for name in ("vllm:num_preemptions_total", "vllm:prefix_cache_hits_total")
        }
        for url in args.backend_urls
    }
    good = [row for row in ordered if row["success"]]
    actual_offsets = sorted(row["actual_offset_s"] for row in ordered)
    positive_waiting_samples = sum(
        any(sample[url]["waiting"] > 0 for url in args.backend_urls) for sample in samples
    )
    max_backend_waiting = max(
        sample[url]["waiting"] for sample in samples for url in args.backend_urls
    )
    summary = {
        "arm_id": arm_id,
        "policy": policy,
        "rate": args.rate,
        "configured_offered_rate_rps": args.rate,
        "count": args.count,
        "seed": args.seed,
        "beta": FROZEN_BETA,
        "trace_sha256": trace_digest,
        "successes": len(good),
        "duration_s": max(row["actual_offset_s"] + row["e2e_s"] for row in ordered),
        "completion_goodput_rps": len(good)
        / max(row["actual_offset_s"] + row["e2e_s"] for row in ordered),
        "achieved_arrival_rate_rps": (len(actual_offsets) - 1)
        / (actual_offsets[-1] - actual_offsets[0])
        if len(actual_offsets) > 1
        else None,
        "arrival_lag_median_s": percentile([row["arrival_lag_s"] for row in ordered], 0.5),
        "arrival_lag_p95_s": percentile([row["arrival_lag_s"] for row in ordered], 0.95),
        "ttft_p95_s": percentile([row["ttft_s"] for row in good], 0.95) if good else None,
        "tpot_p95_s": percentile([row["tpot_s"] for row in good], 0.95) if good else None,
        "e2e_p95_s": percentile([row["e2e_s"] for row in good], 0.95) if good else None,
        "routing_counts": counts,
        "routed_work": work,
        "work_imbalance_fraction": abs(work["replica1"] - work["replica2"]) / sum(work.values()),
        "queued_events": sum(
            event["event"] == "queued"
            for request_events in events.values()
            for event in request_events
        ),
        "failover_events": sum(
            event["event"] == "failover"
            for request_events in events.values()
            for event in request_events
        ),
        "health_transition_events": sum(
            event["event"] == "health_transition"
            for request_events in events.values()
            for event in request_events
        ),
        "rejections": len(ordered) - len(good),
        "max_backend_waiting": max_backend_waiting,
        "backend_waiting_positive_samples": positive_waiting_samples,
        "backend_waiting_positive_fraction": positive_waiting_samples / len(samples),
        "metric_deltas": metric_deltas,
        "final_accounting": accounting,
    }
    summary["validity"] = validate_arm(summary, ordered, events)
    (arm_dir / "requests.json").write_text(json.dumps(ordered, indent=2) + "\n")
    (arm_dir / "routing-events.json").write_text(json.dumps(events, indent=2) + "\n")
    (arm_dir / "backend-samples.json").write_text(json.dumps(samples, indent=2) + "\n")
    (arm_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return summary


async def main_async(args: argparse.Namespace) -> None:
    for policy in args.policies:
        await run_arm(args, policy)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("smoke", "pilot", "measured"), required=True)
    parser.add_argument("--workload", choices=("homogeneous", "mixed"), default="mixed")
    parser.add_argument("--rate", type=float, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--backend-urls",
        nargs=2,
        default=("http://127.0.0.1:8101", "http://127.0.0.1:8102"),
    )
    parser.add_argument("--gateway-port", type=int, default=8080)
    parser.add_argument(
        "--policies", nargs="+", choices=("round_robin", "work_aware"), required=True
    )
    args = parser.parse_args()
    if args.stage in {"smoke", "pilot"} and args.seed not in PILOT_SEEDS:
        raise SystemExit("seed must be reserved for the pilot")
    if args.stage == "measured" and args.seed not in MEASURED_SEEDS:
        raise SystemExit("seed must be reserved for measured runs")
    if args.stage == "pilot" and args.count != 64:
        raise SystemExit("pilot count must be 64")
    if args.stage == "measured" and args.workload == "homogeneous" and args.count != 128:
        raise SystemExit("measured homogeneous count must be 128")
    if args.stage == "measured" and args.workload == "mixed" and args.count != 256:
        raise SystemExit("measured mixed count must be 256")
    if not args.tokenizer.is_dir():
        raise SystemExit(f"tokenizer path is not a directory: {args.tokenizer}")
    args.output_root.mkdir(parents=True, exist_ok=False)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
