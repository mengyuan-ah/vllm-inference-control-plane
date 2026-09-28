from __future__ import annotations

import asyncio
import codecs
import contextlib
import json
import time
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from .admission import AdmissionController, AdmissionError
from .config import GatewayConfig
from .lifecycle import ReplicaLifecycleManager
from .models import ReplicaLifecycle, RequestEnvelope, RequestState
from .prediction import CountAdmissionPolicy, ShadowPredictor, undefined_prediction
from .slowdown import SlowReplicaDetector
from .tracker import ReplicaTracker, Reservation
from .work import LinearWorkEstimator, TokenCounter, WhitespaceTokenCounter, WorkEstimator

GATEWAY_KEY = web.AppKey("gateway", object)


class PrestreamFailure(Exception):
    def __init__(
        self,
        message: str,
        identity: tuple[ReplicaLifecycle, int, int],
    ):
        super().__init__(message)
        self.identity = identity


class MidstreamFailure(Exception):
    pass


class _IncrementalSSELineParser:
    """Extract complete SSE lines without changing the forwarded byte stream."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buffer = ""

    def feed(self, chunk: bytes) -> list[str]:
        self._buffer += self._decoder.decode(chunk)
        lines: list[str] = []
        start = 0
        index = 0
        while index < len(self._buffer):
            character = self._buffer[index]
            if character == "\n":
                lines.append(self._buffer[start:index])
                start = index + 1
            elif character == "\r":
                if index + 1 == len(self._buffer):
                    break
                lines.append(self._buffer[start:index])
                if self._buffer[index + 1] == "\n":
                    index += 1
                start = index + 1
            index += 1
        self._buffer = self._buffer[start:]
        return lines


class Gateway:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        token_counter: TokenCounter | None = None,
        work_estimator: WorkEstimator | None = None,
        shadow_predictor: ShadowPredictor | None = None,
        prediction_admission_policy: CountAdmissionPolicy | None = None,
    ):
        self.config = config
        self.token_counter = token_counter or WhitespaceTokenCounter()
        self.work_estimator = work_estimator or LinearWorkEstimator(config.alpha, config.beta)
        self.tracker = ReplicaTracker(config.replicas, config.routing_mode, config.beta)
        self.admission = AdmissionController(
            self.tracker,
            max_requests=config.max_queued_requests,
            max_work=config.max_queued_work,
            max_residence=config.max_queue_residence,
            prediction_decider=(
                prediction_admission_policy.decide
                if prediction_admission_policy is not None
                else None
            ),
            enforce_prediction=config.prediction_admission_mode == "enforce",
        )
        self.session: ClientSession | None = None
        self.records: dict[str, RequestEnvelope] = {}
        self.shadow_predictor = shadow_predictor
        self.lifecycle = ReplicaLifecycleManager(config, self.tracker)
        self.slowdown = SlowReplicaDetector(self.tracker, config.slowdown_enabled)

    async def start(self) -> None:
        self.session = ClientSession(timeout=ClientTimeout(total=self.config.backend_timeout))
        if self.config.lifecycle_enabled:
            await self.lifecycle.start()

    async def close(self) -> None:
        await self.lifecycle.close()
        if self.session is not None:
            await self.session.close()

    def _validate(self, envelope: RequestEnvelope, body: Any) -> None:
        if not isinstance(body, dict):
            raise ValueError("request body must be an object")
        if body.get("stream") is not True:
            raise ValueError("Stage A requires stream=true")
        if not isinstance(body.get("prompt"), str):
            raise ValueError("prompt must be a string")
        max_tokens = body.get("max_tokens")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        input_tokens = self.token_counter.count(body["prompt"])
        total_work, prefill_work = self.work_estimator.estimate(input_tokens, max_tokens)
        envelope.body = dict(body)
        envelope.input_tokens = input_tokens
        envelope.max_tokens = max_tokens
        envelope.total_work = total_work
        envelope.prefill_work = prefill_work

    async def handle(self, request: web.Request) -> web.StreamResponse:
        request_id = request.headers.get("X-Request-Id") or request.headers.get("X-Request-ID")
        request_id = request_id or f"gateway-{time.monotonic_ns()}"
        envelope = RequestEnvelope({}, 0, 0, 0, 0, request_id=request_id)
        self.records[request_id] = envelope
        envelope.transition(RequestState.VALIDATING)
        try:
            body = await request.json()
            self._validate(envelope, body)
        except asyncio.CancelledError:
            envelope.transition(RequestState.CANCELLED, "client_cancelled")
            raise
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            envelope.transition(RequestState.REJECTED_INVALID, str(exc))
            return self._error(400, "invalid_request", request_id)

        envelope.timing.validation_complete = time.monotonic()
        envelope.transition(RequestState.ADMISSION_CHECK)
        try:
            reservation = await self._admit_with_disconnect(envelope, request)
        except AdmissionError as exc:
            return self._error(exc.status, exc.reason, request_id)
        except asyncio.CancelledError:
            if not envelope.terminal:
                envelope.transition(RequestState.CANCELLED, "client_cancelled")
            raise

        if self.shadow_predictor is not None:
            try:
                prediction = self.shadow_predictor.score(reservation)
            except Exception as exc:
                prediction = undefined_prediction(
                    reservation,
                    predictor_version="scorer-error",
                    error=exc,
                )
            envelope.routing_events.append(prediction)

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Gateway-Request-Id": request_id,
            },
        )
        await response.prepare(request)
        try:
            await self._proxy_with_retry(envelope, reservation, response, request)
        except (ConnectionResetError, asyncio.CancelledError):
            if not envelope.terminal:
                envelope.transition(RequestState.CANCELLED, "client_cancelled")
            raise
        except MidstreamFailure as exc:
            if not envelope.terminal:
                envelope.transition(RequestState.FAILED, str(exc))
            error = json.dumps({"error": {"code": "backend_stream_failed", "message": str(exc)}})
            try:
                await response.write(f"event: error\ndata: {error}\n\n".encode())
            except ConnectionResetError:
                pass
        finally:
            envelope.routing_events.append(
                {"event": "final_accounting", "state": await self.reconcile()}
            )
            try:
                await response.write_eof()
            except (ConnectionResetError, RuntimeError):
                pass
        return response

    async def _admit_with_disconnect(
        self, envelope: RequestEnvelope, request: web.Request
    ) -> Reservation:
        admission_task = asyncio.create_task(self.admission.admit(envelope))
        disconnect_task = asyncio.create_task(self._wait_for_disconnect(request))
        done, _ = await asyncio.wait(
            {admission_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if admission_task in done:
            disconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await disconnect_task
            return await admission_task
        admission_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await admission_task
        if not envelope.terminal:
            envelope.transition(RequestState.CANCELLED, "client_cancelled")
        raise asyncio.CancelledError

    @staticmethod
    async def _wait_for_disconnect(request: web.Request) -> None:
        while request.transport is not None and not request.transport.is_closing():  # noqa: ASYNC110
            await asyncio.sleep(0.01)

    async def _proxy_with_retry(
        self,
        envelope: RequestEnvelope,
        reservation: Reservation,
        response: web.StreamResponse,
        request: web.Request,
    ) -> None:
        failed: set[str] = set()
        current = reservation
        while True:
            try:
                await self._proxy_once(envelope, current, response, request)
                envelope.transition(RequestState.COMPLETED)
                return
            except PrestreamFailure as exc:
                failed.add(current.replica_id)
                await current.release()
                await self.tracker.record_passive_failure(
                    current.replica_id,
                    self.config.passive_failure_threshold,
                    expected=exc.identity,
                )
                envelope.transition(RequestState.RETRY_CHECK, str(exc))
                if envelope.retry_count >= self.config.backend_retry_limit:
                    envelope.transition(RequestState.FAILED, str(exc))
                    raise MidstreamFailure(str(exc)) from exc
                envelope.routing_events.append(
                    {"event": "failover", "failed_replica": current.replica_id}
                )
                alternate = await self.tracker.reserve(
                    envelope.total_work,
                    exclude=failed,
                    request_id=envelope.request_id,
                    metadata={
                        "input_tokens": envelope.input_tokens,
                        "requested_output_tokens": envelope.max_tokens,
                        "estimated_request_work": envelope.total_work,
                    },
                    events=envelope.routing_events,
                )
                if alternate is None:
                    envelope.transition(RequestState.FAILED, str(exc))
                    raise MidstreamFailure(str(exc)) from exc
                envelope.retry_count += 1
                envelope.transition(RequestState.RESERVED)
                envelope.assigned_replica = alternate.replica_id
                envelope.timing.dispatch = time.monotonic()
                current = alternate
            finally:
                if current.released is False and envelope.terminal:
                    await current.release()

    async def _proxy_once(
        self,
        envelope: RequestEnvelope,
        reservation: Reservation,
        response: web.StreamResponse,
        request: web.Request,
    ) -> None:
        assert self.session is not None
        replica = self.tracker.replicas[reservation.replica_id]
        envelope.transition(RequestState.AWAITING_FIRST_CONTENT)
        attempt_dispatch = time.monotonic()
        attempt_identity = await self.tracker.identity(reservation.replica_id)
        _, attempt_generation, attempt_epoch = attempt_identity
        attempt_first_content = None
        saw_content = False
        saw_done = False
        saw_finish = False
        saw_stream_error = False
        parser = _IncrementalSSELineParser()
        backend_response = None
        try:
            envelope.routing_events.append(
                {
                    "timestamp": time.monotonic(),
                    "event": "backend_dispatch",
                    "request_id": envelope.request_id,
                    "selected_replica": reservation.replica_id,
                    "attempt": envelope.retry_count + 1,
                }
            )
            backend_response = await self.session.post(
                f"{replica.config.endpoint}/v1/completions",
                json=envelope.body,
                headers={"X-Request-Id": envelope.request_id},
            )
            if backend_response.status >= 400:
                message = await backend_response.text()
                raise PrestreamFailure(
                    f"backend HTTP {backend_response.status}: {message[:120]}",
                    attempt_identity,
                )
            async for chunk in backend_response.content.iter_any():
                if request.transport is None or request.transport.is_closing():
                    raise asyncio.CancelledError
                for line in parser.feed(chunk):
                    stripped = line.strip()
                    if not stripped.startswith("data:"):
                        continue
                    data = stripped[5:].strip()
                    if data == "[DONE]":
                        saw_done = True
                        continue
                    if not data:
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if event.get("error") is not None:
                        saw_stream_error = True
                    usage = event.get("usage")
                    if usage is not None:
                        envelope.final_usage = usage
                    choices = event.get("choices") or []
                    text = choices[0].get("text", "") if choices else ""
                    if choices and choices[0].get("finish_reason") is not None:
                        saw_finish = True
                    if text and not saw_content:
                        saw_content = True
                        attempt_first_content = time.monotonic()
                        envelope.timing.first_content = attempt_first_content
                        await reservation.first_content(envelope.prefill_work)
                        envelope.transition(RequestState.STREAMING)
                try:
                    await response.write(chunk)
                except ConnectionResetError as exc:
                    # A downstream disconnect is not a failed backend.
                    raise asyncio.CancelledError from exc
                if saw_content and envelope.timing.first_forward is None:
                    envelope.timing.first_forward = time.monotonic()
            envelope.timing.backend_complete = time.monotonic()
            if saw_stream_error:
                raise MidstreamFailure("backend reported a stream error")
            if not saw_content:
                raise PrestreamFailure(
                    "backend ended before non-empty content", attempt_identity
                )
            if not saw_done or not saw_finish or envelope.final_usage is None:
                raise MidstreamFailure("backend stream ended without complete termination")
            if envelope.final_usage.get("completion_tokens") != envelope.max_tokens:
                raise MidstreamFailure("backend stream usage length mismatch")
            await reservation.release()
            if attempt_first_content is not None:
                await self.slowdown.observe(
                    reservation.replica_id,
                    dispatch=attempt_dispatch,
                    first_content=attempt_first_content,
                    generation=attempt_generation,
                    epoch=attempt_epoch,
                    input_tokens=envelope.input_tokens,
                    output_tokens=envelope.max_tokens,
                    request_id=envelope.request_id,
                )
            await self.tracker.record_request_success(
                reservation.replica_id, self.config.recovery_success_threshold
            )
        except asyncio.CancelledError:
            await reservation.release()
            raise
        except (ClientError, TimeoutError, ConnectionError) as exc:
            await reservation.release()
            if saw_content:
                await self.tracker.record_passive_failure(
                    reservation.replica_id,
                    self.config.passive_failure_threshold,
                    expected=attempt_identity,
                )
                raise MidstreamFailure(str(exc)) from exc
            raise PrestreamFailure(str(exc), attempt_identity) from exc
        except PrestreamFailure:
            raise
        except Exception as exc:
            await reservation.release()
            if saw_content:
                await self.tracker.record_passive_failure(
                    reservation.replica_id,
                    self.config.passive_failure_threshold,
                    expected=attempt_identity,
                )
                raise MidstreamFailure(str(exc)) from exc
            raise PrestreamFailure(str(exc), attempt_identity) from exc
        finally:
            if backend_response is not None:
                backend_response.close()

    @staticmethod
    def _error(status: int, code: str, request_id: str) -> web.Response:
        return web.json_response(
            {"error": {"code": code, "gateway_request_id": request_id}}, status=status
        )

    async def reconcile(self) -> dict[str, Any]:
        return {
            "routing_mode": self.config.routing_mode,
            "beta": self.config.beta,
            "queue": await self.admission.snapshot(),
            "replicas": await self.tracker.snapshot(),
            "lifecycle_events": await self.tracker.lifecycle_snapshot(),
            "lifecycle_manager": await self.lifecycle.snapshot(),
        }

    async def accounting(self, _request: web.Request) -> web.Response:
        return web.json_response(await self.reconcile())


def create_app(gateway: Gateway) -> web.Application:
    app = web.Application()
    app[GATEWAY_KEY] = gateway
    app.router.add_post("/v1/completions", gateway.handle)
    app.router.add_get("/_stage_a/accounting", gateway.accounting)
    app.on_startup.append(lambda _app: gateway.start())
    app.on_cleanup.append(lambda _app: gateway.close())
    return app
