from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .config import ReplicaConfig
from .models import SERVING_STATES, ReplicaLifecycle

WORK_ZERO_TOLERANCE = 1e-6


@dataclass
class ReplicaState:
    config: ReplicaConfig
    lifecycle: ReplicaLifecycle = ReplicaLifecycle.HEALTHY
    inflight: int = 0
    outstanding_work: float = 0.0
    passive_failures: int = 0
    recovery_successes: int = 0
    half_open_probe_active: bool = False
    generation: int = 0
    lifecycle_epoch: int = 0

    @property
    def serving_eligible(self) -> bool:
        return self.lifecycle in SERVING_STATES

    @property
    def penalty_metadata(self) -> float:
        return self.config.degraded_penalty if self.lifecycle is ReplicaLifecycle.DEGRADED else 1.0

    def limits(self) -> tuple[int, float]:
        if self.lifecycle is ReplicaLifecycle.DEGRADED:
            return (
                self.config.degraded_max_inflight or self.config.max_inflight,
                self.config.degraded_max_work or self.config.max_work,
            )
        if self.lifecycle is ReplicaLifecycle.RECOVERING:
            return self.config.recovery_max_inflight, self.config.recovery_max_work
        return self.config.max_inflight, self.config.max_work

    def has_headroom(self, work: float) -> bool:
        max_inflight, max_work = self.limits()
        return (
            self.serving_eligible
            and self.inflight < max_inflight
            and self.outstanding_work + work <= max_work
        )


@dataclass
class Reservation:
    tracker: ReplicaTracker
    replica_id: str
    remaining_work: float
    released: bool = False
    prefill_released: bool = False
    request_id: str | None = None
    events: list[dict[str, object]] | None = None
    prediction_features: dict[str, float | int | str] = field(default_factory=dict)
    reservation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    async def first_content(self, prefill_work: float) -> None:
        if self.released or self.prefill_released:
            return
        await self.tracker.release_prefill(self, prefill_work)

    async def release(self) -> bool:
        return await self.tracker.release(self)


@dataclass(frozen=True)
class PredictionAdmissionRejection:
    replica_id: str
    observed_count: int
    threshold: int


class ReplicaTracker:
    def __init__(
        self,
        configs: tuple[ReplicaConfig, ...],
        routing_mode: str = "round_robin",
        beta: float = 1.0,
    ):
        self.replicas = {item.replica_id: ReplicaState(item) for item in configs}
        self.order = [item.replica_id for item in configs]
        self.cursor = 0
        self.routing_mode = routing_mode
        self.beta = beta
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], Awaitable[None]]] = []
        self._active_reservations: dict[str, Reservation] = {}
        self.lifecycle_events: list[dict[str, object]] = []

    def add_listener(self, listener: Callable[[], Awaitable[None]]) -> None:
        self._listeners.append(listener)

    async def _notify(self) -> None:
        for listener in self._listeners:
            asyncio.create_task(listener())

    async def reserve(
        self,
        work: float,
        *,
        exclude: set[str] | None = None,
        request_id: str | None = None,
        metadata: dict[str, object] | None = None,
        events: list[dict[str, object]] | None = None,
        prediction_decider: Callable[[dict[str, object]], dict[str, object]] | None = None,
        enforce_prediction: bool = False,
    ) -> Reservation | PredictionAdmissionRejection | None:
        excluded = exclude or set()
        async with self._lock:
            count = len(self.order)
            eligible = [
                self.order[index]
                for index in range(count)
                if self.order[index] not in excluded
                and self.replicas[self.order[index]].has_headroom(work)
            ]
            scores = {
                replica_id: self.replicas[replica_id].outstanding_work
                / self.replicas[replica_id].config.routing_capacity
                for replica_id in eligible
            }
            selected: str | None = None
            tied: list[str] = []
            if eligible and self.routing_mode == "round_robin":
                selected = next(
                    self.order[(self.cursor + offset) % count]
                    for offset in range(count)
                    if self.order[(self.cursor + offset) % count] in eligible
                )
            elif eligible:
                minimum = min(scores.values())
                tied = [replica_id for replica_id in eligible if scores[replica_id] == minimum]
                selected = next(
                    self.order[(self.cursor + offset) % count]
                    for offset in range(count)
                    if self.order[(self.cursor + offset) % count] in tied
                )
            if selected is not None:
                state = self.replicas[selected]
                before = state.outstanding_work
                inflight_before = state.inflight
                feature_timestamp = time.monotonic()
                features: dict[str, object] = {
                    "feature_timestamp": feature_timestamp,
                    "selected_replica": selected,
                    "selected_inflight_before": inflight_before,
                    "selected_outstanding_work_before": before,
                }
                selected_index = self.order.index(selected)
                self.cursor = (selected_index + 1) % count
                if prediction_decider is not None:
                    try:
                        decision = dict(prediction_decider(features))
                        predicted_reject = decision.get("predicted_reject")
                        if predicted_reject is not None and not isinstance(predicted_reject, bool):
                            raise TypeError("predicted_reject must be bool or None")
                        threshold = decision.get("count_threshold")
                        if predicted_reject is True and (
                            not isinstance(threshold, int)
                            or isinstance(threshold, bool)
                            or threshold <= 0
                        ):
                            raise TypeError(
                                "rejecting decision requires a positive integer threshold"
                            )
                    except Exception as exc:
                        predicted_reject = None
                        decision = {
                            "event": "prediction_admission_decision",
                            "decision_timestamp": time.monotonic(),
                            "feature_timestamp": feature_timestamp,
                            "observed_count": inflight_before,
                            "count_threshold": None,
                            "predicted_reject": None,
                            "decision_reason": f"scorer_error:{type(exc).__name__}",
                            "policy_version": "scorer-error",
                        }
                    decision.update(
                        {
                            "request_id": request_id,
                            "selected_replica": selected,
                            "enforcement_enabled": enforce_prediction,
                            "admission_outcome": (
                                "rejected"
                                if enforce_prediction and predicted_reject is True
                                else "fail_open"
                                if predicted_reject is None
                                else "admitted"
                            ),
                        }
                    )
                    if events is not None:
                        events.append(decision)
                    if enforce_prediction and predicted_reject is True:
                        return PredictionAdmissionRejection(
                            replica_id=selected,
                            observed_count=inflight_before,
                            threshold=int(decision["count_threshold"]),
                        )
                state.inflight += 1
                state.outstanding_work += work
                reservation = Reservation(
                    self,
                    selected,
                    work,
                    request_id=request_id,
                    events=events,
                    prediction_features=features,
                )
                self._active_reservations[reservation.reservation_id] = reservation
                self._event(
                    reservation,
                    "reservation",
                    routing_mode=self.routing_mode,
                    frozen_beta=self.beta,
                    lifecycle=state.lifecycle.value,
                    effective_inflight_limit=state.limits()[0],
                    effective_work_limit=state.limits()[1],
                    eligible_replicas=eligible,
                    candidate_scores=scores,
                    selected_replica=selected,
                    tie_candidates=tied,
                    tie_break_selected=selected if len(tied) > 1 else None,
                    outstanding_before=before,
                    inflight_before=inflight_before,
                    outstanding_after=state.outstanding_work,
                    reserved_work=work,
                    **(metadata or {}),
                )
                return reservation
        return None

    @staticmethod
    def _event(reservation: Reservation, event: str, **fields: object) -> None:
        if reservation.events is not None:
            reservation.events.append(
                {
                    "timestamp": time.monotonic(),
                    "event": event,
                    "request_id": reservation.request_id,
                    **fields,
                }
            )

    async def release_prefill(self, reservation: Reservation, prefill_work: float) -> None:
        async with self._lock:
            if reservation.released or reservation.prefill_released:
                return
            state = self.replicas[reservation.replica_id]
            released = min(prefill_work, reservation.remaining_work)
            state.outstanding_work -= released
            reservation.remaining_work -= released
            reservation.prefill_released = True
            self._event(
                reservation,
                "prefill_release",
                replica_id=reservation.replica_id,
                released_work=released,
                remaining_work=reservation.remaining_work,
                outstanding_after=state.outstanding_work,
            )
        await self._notify()

    async def release(self, reservation: Reservation) -> bool:
        async with self._lock:
            if reservation.released:
                return False
            active = self._active_reservations.get(reservation.reservation_id)
            if active is not reservation:
                raise RuntimeError("reservation identity is not active")
            state = self.replicas[reservation.replica_id]
            if state.inflight <= 0:
                raise RuntimeError("reservation release would underflow inflight count")
            released = reservation.remaining_work
            state.inflight -= 1
            state.outstanding_work -= reservation.remaining_work
            reservation.remaining_work = 0.0
            reservation.released = True
            del self._active_reservations[reservation.reservation_id]
            replica_active = any(
                item.replica_id == reservation.replica_id
                for item in self._active_reservations.values()
            )
            if (
                state.inflight == 0
                and not replica_active
                and math.isfinite(state.outstanding_work)
                and abs(state.outstanding_work) <= WORK_ZERO_TOLERANCE
            ):
                state.outstanding_work = 0.0
            self._event(
                reservation,
                "final_release",
                replica_id=reservation.replica_id,
                released_work=released,
                remaining_work=0.0,
                outstanding_after=state.outstanding_work,
                inflight_after=state.inflight,
            )
        await self._notify()
        return True

    async def set_lifecycle(
        self,
        replica_id: str,
        lifecycle: ReplicaLifecycle,
        cause: str = "manual",
        *,
        expected: tuple[ReplicaLifecycle, int, int] | None = None,
    ) -> bool:
        async with self._lock:
            state = self.replicas[replica_id]
            previous = state.lifecycle
            if expected is not None and expected != (
                previous,
                state.generation,
                state.lifecycle_epoch,
            ):
                return False
            if previous is lifecycle:
                return False
            state.lifecycle = lifecycle
            state.lifecycle_epoch += 1
            state.recovery_successes = 0
            if lifecycle is ReplicaLifecycle.UNHEALTHY:
                state.half_open_probe_active = False
            self.lifecycle_events.append(
                {
                    "event": "replica_state_transition",
                    "timestamp": time.monotonic(),
                    "replica_id": replica_id,
                    "from": previous.value,
                    "to": lifecycle.value,
                    "cause": cause,
                    "generation": state.generation,
                }
            )
        await self._notify()
        return True

    async def identity(self, replica_id: str) -> tuple[ReplicaLifecycle, int, int]:
        async with self._lock:
            state = self.replicas[replica_id]
            return state.lifecycle, state.generation, state.lifecycle_epoch

    async def record_passive_failure(
        self,
        replica_id: str,
        threshold: int = 1,
        *,
        expected: tuple[ReplicaLifecycle, int, int] | None = None,
    ) -> bool:
        async with self._lock:
            state = self.replicas[replica_id]
            identity = (state.lifecycle, state.generation, state.lifecycle_epoch)
            if expected is not None and expected != identity:
                self.lifecycle_events.append(
                    {
                        "event": "stale_passive_failure_ignored",
                        "timestamp": time.monotonic(),
                        "replica_id": replica_id,
                        "expected_lifecycle": expected[0].value,
                        "expected_generation": expected[1],
                        "expected_lifecycle_epoch": expected[2],
                        "current_lifecycle": state.lifecycle.value,
                        "current_generation": state.generation,
                        "current_lifecycle_epoch": state.lifecycle_epoch,
                    }
                )
                return False
            state.passive_failures += 1
            should_exclude = (
                state.lifecycle in SERVING_STATES and state.passive_failures >= threshold
            )
        if should_exclude:
            return await self.set_lifecycle(
                replica_id,
                ReplicaLifecycle.UNHEALTHY,
                "passive_request_failure",
                expected=identity,
            )
        return False

    async def record_request_success(self, replica_id: str, required: int) -> bool:
        async with self._lock:
            state = self.replicas[replica_id]
            state.passive_failures = 0
            if state.lifecycle is not ReplicaLifecycle.RECOVERING:
                return False
            state.recovery_successes += 1
            promote = state.recovery_successes >= required
        if promote:
            return await self.set_lifecycle(
                replica_id, ReplicaLifecycle.HEALTHY, "recovery_success_threshold"
            )
        return False

    async def begin_half_open_probe(self, replica_id: str) -> bool:
        async with self._lock:
            state = self.replicas[replica_id]
            if state.lifecycle is not ReplicaLifecycle.HALF_OPEN or state.half_open_probe_active:
                return False
            state.half_open_probe_active = True
            return True

    async def end_half_open_probe(self, replica_id: str) -> None:
        async with self._lock:
            self.replicas[replica_id].half_open_probe_active = False

    async def new_generation(self, replica_id: str) -> int:
        async with self._lock:
            state = self.replicas[replica_id]
            state.generation += 1
            return state.generation

    async def lifecycle_snapshot(self) -> list[dict[str, object]]:
        async with self._lock:
            return [dict(event) for event in self.lifecycle_events]

    async def serving_exists(self) -> bool:
        async with self._lock:
            return any(item.serving_eligible for item in self.replicas.values())

    async def snapshot(self) -> dict[str, dict[str, float | int | str]]:
        async with self._lock:
            return {
                key: {
                    "lifecycle": value.lifecycle.value,
                    "inflight": value.inflight,
                    "active_reservations": sum(
                        reservation.replica_id == key
                        for reservation in self._active_reservations.values()
                    ),
                    "outstanding_work": value.outstanding_work,
                    "passive_failures": value.passive_failures,
                }
                for key, value in self.replicas.items()
            }
