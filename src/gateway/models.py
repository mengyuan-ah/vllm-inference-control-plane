from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RequestState(StrEnum):
    RECEIVED = "Received"
    VALIDATING = "Validating"
    ADMISSION_CHECK = "AdmissionCheck"
    QUEUED = "Queued"
    RESERVED = "Reserved"
    AWAITING_FIRST_CONTENT = "AwaitingFirstContent"
    STREAMING = "Streaming"
    RETRY_CHECK = "RetryCheck"
    COMPLETED = "Completed"
    REJECTED_INVALID = "RejectedInvalid"
    REJECTED_429 = "Rejected429"
    REJECTED_503 = "Rejected503"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


TERMINAL_STATES = {
    RequestState.COMPLETED,
    RequestState.REJECTED_INVALID,
    RequestState.REJECTED_429,
    RequestState.REJECTED_503,
    RequestState.FAILED,
    RequestState.CANCELLED,
}

TRANSITIONS = {
    RequestState.RECEIVED: {RequestState.VALIDATING},
    RequestState.VALIDATING: {
        RequestState.ADMISSION_CHECK,
        RequestState.REJECTED_INVALID,
        RequestState.CANCELLED,
    },
    RequestState.ADMISSION_CHECK: {
        RequestState.RESERVED,
        RequestState.QUEUED,
        RequestState.REJECTED_429,
        RequestState.REJECTED_503,
        RequestState.CANCELLED,
    },
    RequestState.QUEUED: {
        RequestState.RESERVED,
        RequestState.REJECTED_429,
        RequestState.REJECTED_503,
        RequestState.CANCELLED,
    },
    RequestState.RESERVED: {
        RequestState.AWAITING_FIRST_CONTENT,
        RequestState.RETRY_CHECK,
        RequestState.CANCELLED,
    },
    RequestState.AWAITING_FIRST_CONTENT: {
        RequestState.STREAMING,
        RequestState.RETRY_CHECK,
        RequestState.CANCELLED,
    },
    RequestState.RETRY_CHECK: {
        RequestState.RESERVED,
        RequestState.FAILED,
        RequestState.CANCELLED,
    },
    RequestState.STREAMING: {
        RequestState.COMPLETED,
        RequestState.FAILED,
        RequestState.CANCELLED,
    },
}


class ReplicaLifecycle(StrEnum):
    HEALTHY = "Healthy"
    DEGRADED = "Degraded"
    UNHEALTHY = "Unhealthy"
    HALF_OPEN = "HalfOpen"
    RECOVERING = "Recovering"


SERVING_STATES = {
    ReplicaLifecycle.HEALTHY,
    ReplicaLifecycle.DEGRADED,
    ReplicaLifecycle.RECOVERING,
}


@dataclass
class Timing:
    gateway_arrival: float = field(default_factory=time.monotonic)
    validation_complete: float | None = None
    enqueue: float | None = None
    dispatch: float | None = None
    first_content: float | None = None
    first_forward: float | None = None
    backend_complete: float | None = None
    completion: float | None = None

    def measurements(self) -> dict[str, float | None]:
        preprocessing = (
            self.validation_complete - self.gateway_arrival
            if self.validation_complete is not None
            else None
        )
        queue_residence = (
            self.dispatch - self.enqueue
            if self.dispatch is not None and self.enqueue is not None
            else 0.0
            if self.dispatch is not None
            else None
        )
        return {
            "gateway_preprocessing": preprocessing,
            "queue_residence": queue_residence,
            "gateway_pre_dispatch": (
                self.dispatch - self.gateway_arrival if self.dispatch is not None else None
            ),
            "backend_ttft": (
                self.first_content - self.dispatch
                if self.first_content is not None and self.dispatch is not None
                else None
            ),
            "backend_e2e": (
                self.backend_complete - self.dispatch
                if self.backend_complete is not None and self.dispatch is not None
                else None
            ),
            "gateway_forwarding_delay": (
                self.first_forward - self.first_content
                if self.first_forward is not None and self.first_content is not None
                else None
            ),
        }


@dataclass
class RequestEnvelope:
    body: dict[str, Any]
    input_tokens: int
    max_tokens: int
    total_work: float
    prefill_work: float
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: RequestState = RequestState.RECEIVED
    state_history: list[RequestState] = field(default_factory=lambda: [RequestState.RECEIVED])
    timing: Timing = field(default_factory=Timing)
    retry_count: int = 0
    assigned_replica: str | None = None
    terminal_reason: str | None = None
    final_usage: dict[str, int] | None = None
    routing_events: list[dict[str, Any]] = field(default_factory=list)

    def transition(self, target: RequestState, reason: str | None = None) -> None:
        if self.state in TERMINAL_STATES:
            raise RuntimeError(f"request already terminal: {self.state}")
        if target not in TRANSITIONS.get(self.state, set()):
            raise RuntimeError(f"invalid transition: {self.state} -> {target}")
        self.state = target
        self.state_history.append(target)
        if target in TERMINAL_STATES:
            self.terminal_reason = reason
            self.timing.completion = time.monotonic()

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES
