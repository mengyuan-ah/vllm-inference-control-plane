from __future__ import annotations

import asyncio
import json

from aiohttp import web


class FakeBackend:
    def __init__(self, replica_id: str):
        self.replica_id = replica_id
        self.requests = 0
        self.cancelled = 0
        self.healthy = True
        self.request_started = asyncio.Event()

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/health", self.health)
        app.router.add_post("/v1/completions", self.completions)
        return app

    async def health(self, _request: web.Request) -> web.Response:
        return web.Response(status=200 if self.healthy else 503)

    async def completions(self, request: web.Request) -> web.StreamResponse:
        self.requests += 1
        self.request_started.set()
        body = await request.json()
        mode = body.get("test_mode", "normal")
        first_delay = float(body.get("test_first_delay", 0))
        event_delay = float(body.get("test_event_delay", 0))
        if mode in {"before_headers", "unavailable"} or (
            mode == "fail_replica1" and self.replica_id == "replica1"
        ):
            return web.json_response({"error": mode}, status=503)

        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", "X-Fake-Replica": self.replica_id},
        )
        after_headers = mode == "after_headers" or (
            mode == "fail_replica1_after_headers" and self.replica_id == "replica1"
        ) or (
            mode == "delayed_fail_replica1_after_headers" and self.replica_id == "replica1"
        )
        fragmented_failure = (
            mode == "fragmented_content_then_fail_replica1"
            and self.replica_id == "replica1"
        )
        if after_headers or mode == "midstream" or fragmented_failure:
            response.content_length = 10_000
        await response.prepare(request)
        try:
            if after_headers:
                await response.write(b'data: {"choices":[]}\n\n')
                await asyncio.sleep(float(body.get("test_failure_delay", 0)))
                request.transport.close()
                return response
            await asyncio.sleep(first_delay)
            if mode == "heartbeat_before_content":
                await response.write(b': heartbeat\n\ndata:\n\ndata: {"choices":[]}\n\n')
                await asyncio.sleep(event_delay)
            first = {
                "id": "fake",
                "choices": [{"text": "hello", "finish_reason": None}],
                "usage": None,
            }
            final = {
                "id": "fake",
                "choices": [{"text": " world", "finish_reason": "length"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            }
            first_line = f"data: {json.dumps(first, separators=(',', ':'))}\n\n".encode()
            final_line = f"data: {json.dumps(final, separators=(',', ':'))}\n\n".encode()
            done_line = b"data: [DONE]\n\n"

            if mode == "content_line_before_blank":
                await self._write_parts(response, (first_line[:-1], b"\n", final_line, done_line))
                await response.write_eof()
                return response
            if mode == "split_data_prefix":
                await self._write_parts(
                    response, (b"data:", first_line[len(b"data:") :], final_line, done_line)
                )
                await response.write_eof()
                return response
            if mode == "split_json":
                first_split = len(first_line) // 2
                await self._write_parts(
                    response,
                    (
                        first_line[:9],
                        first_line[9:first_split],
                        first_line[first_split:],
                        final_line,
                        done_line,
                    ),
                )
                await response.write_eof()
                return response
            if mode == "batched_events":
                await response.write(first_line + final_line + done_line)
                await response.write_eof()
                return response
            if mode == "split_done":
                await self._write_parts(
                    response, (first_line, final_line, b"data: [DO", b"NE]\n\n")
                )
                await response.write_eof()
                return response
            if fragmented_failure:
                await self._write_parts(response, (first_line[:5], first_line[5:-1], b"\n"))
                request.transport.close()
                return response

            await response.write(first_line)
            if mode == "midstream":
                request.transport.close()
                return response
            await asyncio.sleep(event_delay)
            await response.write(final_line)
            await response.write(done_line)
            await response.write_eof()
        except (ConnectionResetError, asyncio.CancelledError):
            self.cancelled += 1
            raise
        return response

    @staticmethod
    async def _write_parts(response: web.StreamResponse, parts: tuple[bytes, ...]) -> None:
        for part in parts:
            await response.write(part)
            await asyncio.sleep(0)
