from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReplicaConfig:
    replica_id: str
    endpoint: str
    max_inflight: int = 8
    max_work: float = 100_000.0
    recovery_max_inflight: int = 1
    recovery_max_work: float = 20_000.0
    degraded_max_inflight: int | None = None
    degraded_max_work: float | None = None
    degraded_penalty: float = 1.0
    routing_capacity: float = 1.0


@dataclass(frozen=True)
class GatewayConfig:
    replicas: tuple[ReplicaConfig, ...] = field(
        default_factory=lambda: (
            ReplicaConfig("replica1", "http://127.0.0.1:8101"),
            ReplicaConfig("replica2", "http://127.0.0.1:8102"),
        )
    )
    max_queued_requests: int = 32
    max_queued_work: float = 200_000.0
    max_queue_residence: float = 5.0
    backend_timeout: float = 300.0
    backend_retry_limit: int = 1
    alpha: float = 1.0
    beta: float = 1.0
    routing_mode: str = "round_robin"
    prediction_admission_mode: str = "disabled"
    prediction_admission_count_threshold: int = 8
    slowdown_enabled: bool = False
    lifecycle_enabled: bool = False
    health_probe_interval: float = 0.25
    health_probe_timeout: float = 0.10
    health_failure_threshold: int = 2
    health_success_threshold: int = 2
    passive_failure_threshold: int = 1
    recovery_success_threshold: int = 4
    inference_probe_timeout: float = 2.0
    inference_probe_max_tokens: int = 1

    def __post_init__(self) -> None:
        for replica in self.replicas:
            for reduced, normal in (
                (replica.degraded_max_inflight, replica.max_inflight),
                (replica.degraded_max_work, replica.max_work),
            ):
                if reduced is not None and not 0 < reduced <= normal:
                    raise ValueError(
                        "degraded headroom must be positive and no greater than normal"
                    )
                if self.slowdown_enabled and reduced is None:
                    raise ValueError("slowdown handling requires explicit degraded headroom")
        if self.routing_mode not in {"round_robin", "work_aware"}:
            raise ValueError("routing_mode must be round_robin or work_aware")
        if any(replica.routing_capacity <= 0 for replica in self.replicas):
            raise ValueError("replica routing_capacity must be positive")
        if self.prediction_admission_mode not in {"disabled", "shadow", "enforce"}:
            raise ValueError("prediction_admission_mode must be disabled, shadow, or enforce")
        if self.prediction_admission_count_threshold <= 0:
            raise ValueError("prediction_admission_count_threshold must be positive")
        if self.backend_retry_limit not in {0, 1}:
            raise ValueError("backend_retry_limit must be zero or one")
        positive = {
            "health_probe_interval": self.health_probe_interval,
            "health_probe_timeout": self.health_probe_timeout,
            "health_failure_threshold": self.health_failure_threshold,
            "health_success_threshold": self.health_success_threshold,
            "passive_failure_threshold": self.passive_failure_threshold,
            "recovery_success_threshold": self.recovery_success_threshold,
            "inference_probe_timeout": self.inference_probe_timeout,
            "inference_probe_max_tokens": self.inference_probe_max_tokens,
        }
        if any(value <= 0 for value in positive.values()):
            raise ValueError("lifecycle thresholds and timeouts must be positive")
