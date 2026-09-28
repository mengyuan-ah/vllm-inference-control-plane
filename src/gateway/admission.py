from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass

from .models import RequestEnvelope, RequestState
from .tracker import PredictionAdmissionRejection, ReplicaTracker, Reservation


class AdmissionError(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass
class QueueEntry:
    envelope: RequestEnvelope
    future: asyncio.Future[Reservation]


class AdmissionController:
    def __init__(
        self,
        tracker: ReplicaTracker,
        *,
        max_requests: int,
        max_work: float,
        max_residence: float,
        prediction_decider=None,
        enforce_prediction: bool = False,
    ):
        self.tracker = tracker
        self.max_requests = max_requests
        self.max_work = max_work
        self.max_residence = max_residence
        self.prediction_decider = prediction_decider
        self.enforce_prediction = enforce_prediction
        self.entries: deque[QueueEntry] = deque()
        self.queued_work = 0.0
        self.peak_queued_requests = 0
        self.peak_queued_work = 0.0
        self._lock = asyncio.Lock()
        tracker.add_listener(self.drain)

    async def _reserve(
        self, envelope: RequestEnvelope, *, exclude: set[str] | None = None
    ) -> Reservation | PredictionAdmissionRejection | None:
        return await self.tracker.reserve(
            envelope.total_work,
            exclude=exclude,
            request_id=envelope.request_id,
            metadata={
                "input_tokens": envelope.input_tokens,
                "requested_output_tokens": envelope.max_tokens,
                "estimated_request_work": envelope.total_work,
            },
            events=envelope.routing_events,
            prediction_decider=self.prediction_decider,
            enforce_prediction=self.enforce_prediction,
        )

    async def admit(self, envelope: RequestEnvelope) -> Reservation:
        reservation = await self._reserve(envelope)
        if isinstance(reservation, PredictionAdmissionRejection):
            envelope.transition(RequestState.REJECTED_429, "prediction_count_threshold")
            envelope.routing_events.append(
                {
                    "timestamp": time.monotonic(),
                    "event": "rejection",
                    "outcome": "prediction_count_threshold",
                    "selected_replica": reservation.replica_id,
                    "observed_count": reservation.observed_count,
                    "count_threshold": reservation.threshold,
                }
            )
            raise AdmissionError(429, "prediction_count_threshold")
        if reservation is not None:
            envelope.transition(RequestState.RESERVED)
            envelope.assigned_replica = reservation.replica_id
            envelope.timing.dispatch = time.monotonic()
            return reservation
        if not await self.tracker.serving_exists():
            envelope.transition(RequestState.REJECTED_503, "no_healthy_replica")
            envelope.routing_events.append({"event": "rejection", "outcome": "no_healthy_replica"})
            raise AdmissionError(503, "no_healthy_replica")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Reservation] = loop.create_future()
        async with self._lock:
            if len(self.entries) >= self.max_requests:
                envelope.transition(RequestState.REJECTED_429, "queue_request_limit")
                envelope.routing_events.append(
                    {"event": "rejection", "outcome": "queue_request_limit"}
                )
                raise AdmissionError(429, "queue_request_limit")
            if self.queued_work + envelope.total_work > self.max_work:
                envelope.transition(RequestState.REJECTED_429, "queue_work_limit")
                envelope.routing_events.append(
                    {"event": "rejection", "outcome": "queue_work_limit"}
                )
                raise AdmissionError(429, "queue_work_limit")
            envelope.transition(RequestState.QUEUED)
            envelope.timing.enqueue = time.monotonic()
            self.entries.append(QueueEntry(envelope, future))
            self.queued_work += envelope.total_work
            self.peak_queued_requests = max(self.peak_queued_requests, len(self.entries))
            self.peak_queued_work = max(self.peak_queued_work, self.queued_work)
            envelope.routing_events.append(
                {"timestamp": time.monotonic(), "event": "queued", "outcome": "waiting"}
            )

        # Capacity may have become available after the failed immediate reservation but
        # before this entry was appended. Rechecking after enqueue closes that lost-wakeup
        # window; concurrent listener drains remain serialized by the admission lock.
        await self.drain()

        try:
            return await asyncio.wait_for(asyncio.shield(future), self.max_residence)
        except TimeoutError as exc:
            if await self._remove(envelope):
                envelope.transition(RequestState.REJECTED_429, "queue_timeout")
                envelope.routing_events.append({"event": "rejection", "outcome": "queue_timeout"})
                raise AdmissionError(429, "queue_timeout") from exc
            return await future
        except asyncio.CancelledError:
            if await self._remove(envelope):
                envelope.transition(RequestState.CANCELLED, "client_cancelled")
            else:
                try:
                    reservation = await future
                except AdmissionError:
                    pass
                else:
                    await reservation.release()
                if not envelope.terminal:
                    envelope.transition(RequestState.CANCELLED, "client_cancelled")
            raise

    async def _remove(self, envelope: RequestEnvelope) -> bool:
        async with self._lock:
            for entry in self.entries:
                if entry.envelope is envelope:
                    self.entries.remove(entry)
                    self.queued_work = max(0.0, self.queued_work - envelope.total_work)
                    if not entry.future.done():
                        entry.future.cancel()
                    return True
        return False

    async def drain(self) -> None:
        async with self._lock:
            while self.entries:
                entry = self.entries[0]
                if entry.future.done():
                    self.entries.popleft()
                    self.queued_work = max(0.0, self.queued_work - entry.envelope.total_work)
                    continue
                if not await self.tracker.serving_exists():
                    self.entries.popleft()
                    self.queued_work = max(0.0, self.queued_work - entry.envelope.total_work)
                    entry.envelope.transition(RequestState.REJECTED_503, "replicas_unavailable")
                    entry.envelope.routing_events.append(
                        {"event": "rejection", "outcome": "replicas_unavailable"}
                    )
                    entry.future.set_exception(AdmissionError(503, "replicas_unavailable"))
                    continue
                if self._expired(entry):
                    self._pop_head(entry)
                    self._reject_timeout(entry)
                    continue
                reservation = await self._reserve(entry.envelope)
                if reservation is None:
                    break
                if isinstance(reservation, PredictionAdmissionRejection):
                    self.entries.popleft()
                    self.queued_work = max(0.0, self.queued_work - entry.envelope.total_work)
                    entry.envelope.transition(
                        RequestState.REJECTED_429, "prediction_count_threshold"
                    )
                    entry.envelope.routing_events.append(
                        {"event": "rejection", "outcome": "prediction_count_threshold"}
                    )
                    entry.future.set_exception(AdmissionError(429, "prediction_count_threshold"))
                    continue
                if self._expired(entry):
                    self._pop_head(entry)
                    await reservation.release()
                    self._reject_timeout(entry)
                    continue
                self.entries.popleft()
                self.queued_work = max(0.0, self.queued_work - entry.envelope.total_work)
                entry.envelope.transition(RequestState.RESERVED)
                entry.envelope.assigned_replica = reservation.replica_id
                entry.envelope.timing.dispatch = time.monotonic()
                entry.envelope.routing_events.append(
                    {
                        "timestamp": entry.envelope.timing.dispatch,
                        "event": "queue_dispatch",
                        "queue_residence": (
                            entry.envelope.timing.dispatch - entry.envelope.timing.enqueue
                        ),
                    }
                )
                entry.future.set_result(reservation)

    def _expired(self, entry: QueueEntry) -> bool:
        enqueue = entry.envelope.timing.enqueue
        return enqueue is not None and time.monotonic() - enqueue >= self.max_residence

    def _pop_head(self, entry: QueueEntry) -> None:
        popped = self.entries.popleft()
        if popped is not entry:
            raise RuntimeError("queue head changed while admission lock was held")
        self.queued_work = max(0.0, self.queued_work - entry.envelope.total_work)

    @staticmethod
    def _reject_timeout(entry: QueueEntry) -> None:
        entry.envelope.transition(RequestState.REJECTED_429, "queue_timeout")
        entry.envelope.routing_events.append({"event": "rejection", "outcome": "queue_timeout"})
        entry.future.set_exception(AdmissionError(429, "queue_timeout"))

    async def snapshot(self) -> dict[str, float | int]:
        async with self._lock:
            return {
                "queued_requests": len(self.entries),
                "queued_work": self.queued_work,
                "peak_queued_requests": self.peak_queued_requests,
                "peak_queued_work": self.peak_queued_work,
            }
