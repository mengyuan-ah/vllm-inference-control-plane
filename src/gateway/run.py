from __future__ import annotations

import os

from aiohttp import web

from .app import Gateway, create_app
from .config import GatewayConfig, ReplicaConfig
from .prediction import CountAdmissionPolicy
from .tokenizers import HuggingFaceTokenCounter


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def env_optional_int(name: str) -> int | None:
    value = os.environ.get(name)
    return None if value is None else int(value)


def env_optional_float(name: str) -> float | None:
    value = os.environ.get(name)
    return None if value is None else float(value)


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def replica_from_env(number: int, default_url: str) -> ReplicaConfig:
    prefix = f"GATEWAY_REPLICA{number}"
    return ReplicaConfig(
        f"replica{number}",
        os.environ.get(f"{prefix}_URL", default_url),
        max_inflight=env_int(f"{prefix}_MAX_INFLIGHT", 8),
        max_work=env_float(f"{prefix}_MAX_WORK", 100_000),
        recovery_max_inflight=env_int(f"{prefix}_RECOVERY_MAX_INFLIGHT", 1),
        recovery_max_work=env_float(f"{prefix}_RECOVERY_MAX_WORK", 20_000),
        degraded_max_inflight=env_optional_int(f"{prefix}_DEGRADED_MAX_INFLIGHT"),
        degraded_max_work=env_optional_float(f"{prefix}_DEGRADED_MAX_WORK"),
        degraded_penalty=env_float(f"{prefix}_DEGRADED_PENALTY", 1.0),
        routing_capacity=env_float(f"{prefix}_ROUTING_CAPACITY", 1.0),
    )


def config_from_env() -> GatewayConfig:
    replicas = [replica_from_env(1, "http://127.0.0.1:8101")]
    if env_bool("GATEWAY_REPLICA2_ENABLED", True):
        replicas.append(replica_from_env(2, "http://127.0.0.1:8102"))
    return GatewayConfig(
        replicas=tuple(replicas),
        max_queued_requests=env_int("GATEWAY_MAX_QUEUED_REQUESTS", 32),
        max_queued_work=env_float("GATEWAY_MAX_QUEUED_WORK", 200_000),
        max_queue_residence=env_float("GATEWAY_MAX_QUEUE_RESIDENCE", 5),
        backend_timeout=env_float("GATEWAY_BACKEND_TIMEOUT", 300),
        backend_retry_limit=env_int("GATEWAY_BACKEND_RETRY_LIMIT", 1),
        alpha=env_float("GATEWAY_WORK_ALPHA", 1),
        beta=env_float("GATEWAY_WORK_BETA", 1),
        routing_mode=os.environ.get("GATEWAY_ROUTING_MODE", "round_robin"),
        prediction_admission_mode=os.environ.get("GATEWAY_PREDICTION_ADMISSION_MODE", "disabled"),
        prediction_admission_count_threshold=env_int(
            "GATEWAY_PREDICTION_ADMISSION_COUNT_THRESHOLD", 8
        ),
        slowdown_enabled=env_bool("GATEWAY_SLOWDOWN_ENABLED", False),
        lifecycle_enabled=env_bool("GATEWAY_LIFECYCLE_ENABLED", False),
        health_probe_interval=env_float("GATEWAY_HEALTH_PROBE_INTERVAL", 0.25),
        health_probe_timeout=env_float("GATEWAY_HEALTH_PROBE_TIMEOUT", 0.10),
        health_failure_threshold=env_int("GATEWAY_HEALTH_FAILURE_THRESHOLD", 2),
        health_success_threshold=env_int("GATEWAY_HEALTH_SUCCESS_THRESHOLD", 2),
        passive_failure_threshold=env_int("GATEWAY_PASSIVE_FAILURE_THRESHOLD", 1),
        recovery_success_threshold=env_int("GATEWAY_RECOVERY_SUCCESS_THRESHOLD", 4),
        inference_probe_timeout=env_float("GATEWAY_INFERENCE_PROBE_TIMEOUT", 2.0),
        inference_probe_max_tokens=env_int("GATEWAY_INFERENCE_PROBE_MAX_TOKENS", 1),
    )


def main() -> None:
    model_id = os.environ.get("GATEWAY_TOKENIZER_MODEL")
    token_counter = HuggingFaceTokenCounter(model_id) if model_id else None
    config = config_from_env()
    policy = (
        CountAdmissionPolicy(config.prediction_admission_count_threshold)
        if config.prediction_admission_mode != "disabled"
        else None
    )
    gateway = Gateway(config, token_counter=token_counter, prediction_admission_policy=policy)
    web.run_app(
        create_app(gateway),
        host=os.environ.get("GATEWAY_HOST", "127.0.0.1"),
        port=env_int("GATEWAY_PORT", 8080),
    )
