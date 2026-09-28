import asyncio
import time

import aiohttp
from aiohttp import web

from e11.shim import DelayShim
from run_harness import serve


async def test_byte_preservation_and_responsive_health():
    received = []
    payload = b'data: {"choices":[{"text":"hello"}]}\n\ndata: [DONE]\n\n'

    async def complete(request):
        received.append(await request.read())
        return web.Response(body=payload, content_type="text/event-stream")

    async def health(_request):
        return web.Response(text="healthy")

    app = web.Application()
    app.router.add_post("/v1/completions", complete)
    app.router.add_get("/health", health)
    async with serve(app) as backend:
        shim = DelayShim(backend, "replica1")
        async with serve(shim.app()) as proxy, aiohttp.ClientSession() as client:
            now = time.monotonic()
            shim.arm(now, now + 1, 0.4)
            body = b'{ "prompt": "unaltered", "max_tokens": 128 }'

            async def request():
                async with client.post(proxy + "/v1/completions", data=body) as response:
                    return await response.read()

            task = asyncio.create_task(request())
            await asyncio.sleep(0.02)
            start = time.monotonic()
            async with client.get(proxy + "/health") as response:
                assert await response.text() == "healthy"
            assert time.monotonic() - start < 0.2
            assert await task == payload
            assert received == [body]
            dispatch = next(e for e in shim.events if e["event"] == "shim_dispatch")
            assert dispatch["actual_delay_s"] >= 0.4
    assert shim.session.closed and shim.markers.done()
