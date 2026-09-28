from __future__ import annotations

import asyncio
import time

from aiohttp import ClientSession, ClientTimeout, web


class DelayShim:
    """Delay dispatch once, before upstream POST; never rewrite SSE/body bytes."""

    def __init__(self, upstream: str, replica_id: str):
        self.upstream = upstream.rstrip("/")
        self.replica_id = replica_id
        self.interval: tuple[float, float] | None = None
        self.delay_s = 0.0
        self.events: list[dict] = []
        self.session: ClientSession | None = None
        self.markers: asyncio.Task | None = None

    def arm(self, start: float, end: float, delay_s: float) -> None:
        if self.interval is not None or not (0 < delay_s <= 2 and end > start):
            raise ValueError("one bounded injection interval per shim instance")
        self.interval, self.delay_s = (start, end), delay_s
        self.markers = asyncio.create_task(self._markers(start, end))

    async def _markers(self, start: float, end: float) -> None:
        for name, scheduled in (("injection_start", start), ("injection_end", end)):
            await asyncio.sleep(max(0, scheduled - time.monotonic()))
            self.events.append(
                dict(
                    event=name,
                    timestamp=time.monotonic(),
                    scheduled=scheduled,
                    replica_id=self.replica_id,
                    delay_s=self.delay_s,
                )
            )

    async def start(self, _app):
        self.session = ClientSession(timeout=ClientTimeout(total=5), auto_decompress=False)

    async def close(self, _app):
        if self.markers is not None:
            self.markers.cancel()
            await asyncio.gather(self.markers, return_exceptions=True)
        if self.session is not None:
            await self.session.close()

    def app(self):
        app = web.Application()
        app.router.add_get("/health", self.health)
        app.router.add_post("/v1/completions", self.complete)
        app.on_startup.append(self.start)
        app.on_cleanup.append(self.close)
        return app

    async def health(self, _request):
        assert self.session is not None
        async with self.session.get(self.upstream + "/health") as response:
            return web.Response(status=response.status, body=await response.read())

    async def complete(self, request):
        assert self.session is not None
        body = await request.read()
        received = time.monotonic()
        scheduled_delay = (
            self.delay_s
            if self.interval and (self.interval[0] <= received < self.interval[1])
            else 0.0
        )
        started = time.monotonic()
        await asyncio.sleep(scheduled_delay)
        applied = time.monotonic() - started
        self.events.append(
            dict(
                event="shim_dispatch",
                timestamp=time.monotonic(),
                received=received,
                request_id=request.headers.get("X-Request-Id"),
                replica_id=self.replica_id,
                scheduled_delay_s=scheduled_delay,
                actual_delay_s=applied,
            )
        )
        headers = {
            "Content-Type": request.headers.get("Content-Type", "application/json"),
            "Accept-Encoding": "identity",
        }
        if "X-Request-Id" in request.headers:
            headers["X-Request-Id"] = request.headers["X-Request-Id"]
        async with self.session.post(
            self.upstream + "/v1/completions", data=body, headers=headers
        ) as upstream:
            response = web.StreamResponse(
                status=upstream.status,
                headers={
                    key: value
                    for key, value in upstream.headers.items()
                    if key.lower() in {"content-type", "content-encoding", "cache-control"}
                },
            )
            await response.prepare(request)
            async for chunk in upstream.content.iter_any():
                await response.write(chunk)
            await response.write_eof()
            return response
