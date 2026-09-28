from __future__ import annotations

import argparse
import asyncio
import json

from aiohttp import web


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def complete(request: web.Request) -> web.StreamResponse:
    body = await request.json()
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    await asyncio.sleep(float(body.get("test_first_delay", 0.005)))
    first = {"choices": [{"text": "x", "finish_reason": None}], "usage": None}
    final = {
        "choices": [{"text": "", "finish_reason": "length"}],
        "usage": {
            "prompt_tokens": len(body["prompt"]),
            "completion_tokens": body["max_tokens"],
            "total_tokens": len(body["prompt"]) + body["max_tokens"],
        },
    }
    await response.write(f"data: {json.dumps(first)}\n\n".encode())
    await response.write(f"data: {json.dumps(final)}\n\n".encode())
    await response.write(b"data: [DONE]\n\n")
    return response


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/v1/completions", complete)
    web.run_app(app, host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
