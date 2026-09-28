from __future__ import annotations

import asyncio
import math
import time
from collections import deque

from .models import ReplicaLifecycle
from .tracker import ReplicaTracker

# Frozen homogeneous 1024-input / 128-output E11 rule; not a general workload model.
WINDOW_S = 10.0
SAMPLES = 4
DEGRADE_TTFT_S = 0.350
RECOVER_TTFT_S = 0.200


class SlowReplicaDetector:
    def __init__(self, tracker: ReplicaTracker, enabled: bool = False):
        self.tracker = tracker
        self.enabled = enabled
        self.events: list[dict] = []
        self.samples: dict[str, deque] = {}
        self.epochs: dict[str, tuple] = {}
        self._lock = asyncio.Lock()

    async def observe(
        self,
        replica_id: str,
        *,
        dispatch: float,
        first_content: float,
        generation: int,
        epoch: int,
        input_tokens: int,
        output_tokens: int,
        request_id: str,
        now: float | None = None,
    ) -> None:
        now = time.monotonic() if now is None else now
        async with self._lock:
            state = self.tracker.replicas[replica_id]
            identity = (state.lifecycle, state.generation, state.lifecycle_epoch)
            latency = first_content - dispatch
            event = dict(
                event="backend_latency_observation",
                timestamp=now,
                replica_id=replica_id,
                request_id=request_id,
                dispatch=dispatch,
                first_content=first_content,
                backend_ttft_s=latency,
                generation=generation,
                lifecycle_epoch=epoch,
                enabled=self.enabled,
            )
            reason = None
            if not all(math.isfinite(x) for x in (now, dispatch, first_content)) or not (
                0 <= latency and dispatch <= first_content <= now
            ):
                reason = "invalid_timing"
            elif (input_tokens, output_tokens) != (1024, 128):
                reason = "incomparable_workload"
            elif (generation, epoch) != (state.generation, state.lifecycle_epoch):
                reason = "stale_generation_or_lifecycle"
            elif now - dispatch > WINDOW_S:
                reason = "stale_observation"
            elif state.lifecycle not in {ReplicaLifecycle.HEALTHY, ReplicaLifecycle.DEGRADED}:
                reason = "failure_lifecycle_owns_state"
            samples = self.samples.setdefault(replica_id, deque(maxlen=SAMPLES))
            if self.epochs.get(replica_id) != identity:
                samples.clear()
                self.epochs[replica_id] = identity
            fresh = [sample for sample in samples if now - sample[0] <= WINDOW_S]
            samples.clear()
            samples.extend(fresh)
            if reason is None:
                samples.append((dispatch, latency))
                reason = "insufficient_samples" if len(samples) < SAMPLES else "hysteresis_hold"
                target = None
                if len(samples) == SAMPLES:
                    if state.lifecycle is ReplicaLifecycle.HEALTHY and all(
                        value > DEGRADE_TTFT_S for _, value in samples
                    ):
                        target = ReplicaLifecycle.DEGRADED
                    elif state.lifecycle is ReplicaLifecycle.DEGRADED and all(
                        value < RECOVER_TTFT_S for _, value in samples
                    ):
                        target = ReplicaLifecycle.HEALTHY
                event["sample_count"] = len(samples)
                if target is not None:
                    reason = (
                        "four_slow_samples"
                        if target is ReplicaLifecycle.DEGRADED
                        else "four_fast_samples"
                    )
                    event["proposed_state"] = target.value
                    if self.enabled:
                        changed = await self.tracker.set_lifecycle(
                            replica_id, target, "slowdown:" + reason, expected=identity
                        )
                        event["transition_applied"] = changed
                        if changed:
                            samples.clear()
                    else:
                        event["transition_applied"] = False
                        reason = "handling_disabled:" + reason
            event.setdefault("sample_count", len(samples))
            event.update(reason=reason, retained_samples=len(samples))
            self.events.append(event)
