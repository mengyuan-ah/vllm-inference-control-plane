from __future__ import annotations

from gateway.run import config_from_env


def test_single_replica_environment(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_REPLICA1_URL", "http://127.0.0.1:8101")
    monkeypatch.setenv("GATEWAY_REPLICA2_ENABLED", "0")
    config = config_from_env()
    assert len(config.replicas) == 1
    assert config.replicas[0].endpoint == "http://127.0.0.1:8101"


def test_two_replica_environment(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_REPLICA2_ENABLED", "1")
    monkeypatch.setenv("GATEWAY_REPLICA2_URL", "http://127.0.0.1:8102")
    config = config_from_env()
    assert [item.endpoint for item in config.replicas] == [
        "http://127.0.0.1:8101",
        "http://127.0.0.1:8102",
    ]


def test_launcher_exposes_slowdown_routing_and_recovery_headroom(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_REPLICA2_ENABLED", "true")
    monkeypatch.setenv("GATEWAY_SLOWDOWN_ENABLED", "yes")
    for number, values in {
        1: (3, 30_000, 2, 12_000, 1.5),
        2: (4, 40_000, 1, 10_000, 0.75),
    }.items():
        degraded_count, degraded_work, recovery_count, recovery_work, capacity = values
        prefix = f"GATEWAY_REPLICA{number}"
        monkeypatch.setenv(f"{prefix}_DEGRADED_MAX_INFLIGHT", str(degraded_count))
        monkeypatch.setenv(f"{prefix}_DEGRADED_MAX_WORK", str(degraded_work))
        monkeypatch.setenv(f"{prefix}_RECOVERY_MAX_INFLIGHT", str(recovery_count))
        monkeypatch.setenv(f"{prefix}_RECOVERY_MAX_WORK", str(recovery_work))
        monkeypatch.setenv(f"{prefix}_ROUTING_CAPACITY", str(capacity))

    config = config_from_env()

    assert config.slowdown_enabled is True
    assert [
        (
            replica.degraded_max_inflight,
            replica.degraded_max_work,
            replica.recovery_max_inflight,
            replica.recovery_max_work,
            replica.routing_capacity,
        )
        for replica in config.replicas
    ] == [
        (3, 30_000.0, 2, 12_000.0, 1.5),
        (4, 40_000.0, 1, 10_000.0, 0.75),
    ]


def test_launcher_rejects_invalid_boolean(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_REPLICA2_ENABLED", "sometimes")
    try:
        config_from_env()
    except ValueError as exc:
        assert "GATEWAY_REPLICA2_ENABLED must be a boolean value" in str(exc)
    else:
        raise AssertionError("invalid boolean environment value was accepted")
