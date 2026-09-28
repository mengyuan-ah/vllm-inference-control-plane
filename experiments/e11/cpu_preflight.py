from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from aiohttp import web

from analyze import arm_result
from e11.protocol import SETTINGS
from run_harness import run_arm, serve, write


class CharacterTokenizer:
    def encode(self, text, add_special_tokens=False):
        return [ord(c) for c in text]

    def decode(self, tokens, skip_special_tokens=False):
        return "".join(chr(t) for t in tokens)


async def health(_request):
    return web.Response(text="ok")


async def metrics(_request):
    return web.Response(
        text="vllm:num_requests_waiting 0\nvllm:num_preemptions_total 0\n"
        "vllm:prefix_cache_hits_total 0\n"
    )


async def complete(request):
    body = await request.json()
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    await asyncio.sleep(0.005)
    await response.write(b'data: {"choices":[{"text":"x"}]}\n\n')
    await asyncio.sleep(0.04)
    event = dict(
        choices=[dict(text="", finish_reason="length")],
        usage=dict(prompt_tokens=len(body["prompt"]), completion_tokens=body["max_tokens"]),
    )
    await response.write(f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode())
    return response


def fake_app():
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/metrics", metrics)
    app.router.add_post("/v1/completions", complete)
    return app


async def run(output):
    output.mkdir(parents=True, exist_ok=False)
    settings = dict(
        SETTINGS,
        rate=20.0,
        offered=200,
        offer_window_s=10.0,
        slowdown_start_s=1.0,
        slowdown_end_s=6.0,
        delay_s=0.4,
    )
    async with serve(fake_app()) as one, serve(fake_app()) as two:
        for arm in ("A", "B"):
            spec = dict(
                arm_id="cpu-" + arm,
                pair_id="cpu",
                arm=arm,
                seed=119001,
                target="replica1",
                order=0 if arm == "A" else 1,
            )
            await run_arm(
                spec,
                [one, two],
                CharacterTokenizer(),
                output / spec["arm_id"],
                "cpu-fake-only",
                settings=settings,
                stage="cpu",
            )
    a, b = arm_result(output / "cpu-A"), arm_result(output / "cpu-B")
    valid = (
        a["valid"]
        and b["valid"]
        and a["trace_sha256"] == b["trace_sha256"]
        and b["detection_delay_s"] is not None
        and b["recovery_delay_s"] is not None
        and b["false_degradations"] == 0
        and b["phase_dispatch"]["slow"]["target_fraction"]
        < a["phase_dispatch"]["slow"]["target_fraction"]
    )
    result = dict(valid=valid, A=a, B=b, cpu_only=True)
    write(output / "preflight.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.output))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["valid"] else 2)


if __name__ == "__main__":
    main()
