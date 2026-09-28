from __future__ import annotations

import pytest

from gateway.models import (
    SERVING_STATES,
    TERMINAL_STATES,
    ReplicaLifecycle,
    RequestEnvelope,
    RequestState,
)


def envelope() -> RequestEnvelope:
    return RequestEnvelope({"stream": True}, 2, 2, 4, 2)


def test_lifecycle_eligibility_table() -> None:
    assert SERVING_STATES == {
        ReplicaLifecycle.HEALTHY,
        ReplicaLifecycle.DEGRADED,
        ReplicaLifecycle.RECOVERING,
    }
    assert ReplicaLifecycle.UNHEALTHY not in SERVING_STATES
    assert ReplicaLifecycle.HALF_OPEN not in SERVING_STATES


def test_happy_path_reaches_one_terminal_state() -> None:
    item = envelope()
    for state in (
        RequestState.VALIDATING,
        RequestState.ADMISSION_CHECK,
        RequestState.RESERVED,
        RequestState.AWAITING_FIRST_CONTENT,
        RequestState.STREAMING,
        RequestState.COMPLETED,
    ):
        item.transition(state)
    assert item.state is RequestState.COMPLETED
    assert sum(state in TERMINAL_STATES for state in item.state_history) == 1
    with pytest.raises(RuntimeError, match="already terminal"):
        item.transition(RequestState.FAILED)


@pytest.mark.parametrize(
    "path",
    [
        [RequestState.VALIDATING, RequestState.CANCELLED],
        [RequestState.VALIDATING, RequestState.ADMISSION_CHECK, RequestState.CANCELLED],
        [
            RequestState.VALIDATING,
            RequestState.ADMISSION_CHECK,
            RequestState.QUEUED,
            RequestState.CANCELLED,
        ],
        [
            RequestState.VALIDATING,
            RequestState.ADMISSION_CHECK,
            RequestState.RESERVED,
            RequestState.CANCELLED,
        ],
        [
            RequestState.VALIDATING,
            RequestState.ADMISSION_CHECK,
            RequestState.RESERVED,
            RequestState.AWAITING_FIRST_CONTENT,
            RequestState.CANCELLED,
        ],
        [
            RequestState.VALIDATING,
            RequestState.ADMISSION_CHECK,
            RequestState.RESERVED,
            RequestState.RETRY_CHECK,
            RequestState.CANCELLED,
        ],
        [
            RequestState.VALIDATING,
            RequestState.ADMISSION_CHECK,
            RequestState.RESERVED,
            RequestState.AWAITING_FIRST_CONTENT,
            RequestState.STREAMING,
            RequestState.CANCELLED,
        ],
    ],
)
def test_cancellation_from_every_applicable_state(path: list[RequestState]) -> None:
    item = envelope()
    for state in path:
        item.transition(state)
    assert item.state is RequestState.CANCELLED
    assert sum(state in TERMINAL_STATES for state in item.state_history) == 1


def test_invalid_transition_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="invalid transition"):
        envelope().transition(RequestState.COMPLETED)
