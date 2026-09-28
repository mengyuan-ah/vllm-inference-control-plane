from gateway.config import ReplicaConfig
from gateway.models import ReplicaLifecycle
from gateway.slowdown import SlowReplicaDetector
from gateway.tracker import ReplicaTracker


def setup(enabled=True):
    tracker = ReplicaTracker(
        (ReplicaConfig("r", "http://unused", degraded_max_inflight=1, degraded_max_work=20000),)
    )
    return tracker, SlowReplicaDetector(tracker, enabled)


async def observe(detector, i, latency, *, generation=0, epoch=None):
    state = detector.tracker.replicas["r"]
    await detector.observe(
        "r",
        dispatch=i,
        first_content=i + latency,
        now=i + latency + 0.01,
        generation=generation,
        epoch=state.lifecycle_epoch if epoch is None else epoch,
        input_tokens=1024,
        output_tokens=128,
        request_id=str(i),
    )


async def test_hysteresis_headroom_and_latency_only_recovery():
    tracker, detector = setup()
    for i in range(3):
        await observe(detector, i, 0.5)
    assert tracker.replicas["r"].lifecycle is ReplicaLifecycle.HEALTHY
    await observe(detector, 3, 0.5)
    assert tracker.replicas["r"].lifecycle is ReplicaLifecycle.DEGRADED
    first = await tracker.reserve(14756.0981376)
    assert first is not None and await tracker.reserve(14756.0981376) is None
    await first.release()
    for i in range(4, 7):
        await observe(detector, i, 0.1)
    assert tracker.replicas["r"].lifecycle is ReplicaLifecycle.DEGRADED
    await observe(detector, 7, 0.1)
    assert tracker.replicas["r"].lifecycle is ReplicaLifecycle.HEALTHY
    assert [e["to"] for e in tracker.lifecycle_events] == ["Degraded", "Healthy"]


async def test_disabled_and_hysteresis_band_do_not_change_routing():
    tracker, detector = setup(False)
    for i in range(8):
        await observe(detector, i, 0.5)
    assert tracker.replicas["r"].lifecycle is ReplicaLifecycle.HEALTHY
    assert not tracker.lifecycle_events
    tracker, detector = setup()
    for i in range(8):
        await observe(detector, i, 0.25)
    assert not tracker.lifecycle_events


async def test_stale_samples_generations_and_lifecycle_cannot_promote():
    tracker, detector = setup()
    for i in range(4):
        await observe(detector, i, 0.5)
    epoch = tracker.replicas["r"].lifecycle_epoch
    for i in range(4, 8):
        await observe(detector, i, 0.1, epoch=epoch - 1)
    assert tracker.replicas["r"].lifecycle is ReplicaLifecycle.DEGRADED
    await tracker.new_generation("r")
    for i in range(8, 12):
        await observe(detector, i, 0.1, generation=0)
    assert tracker.replicas["r"].lifecycle is ReplicaLifecycle.DEGRADED
    await tracker.set_lifecycle("r", ReplicaLifecycle.UNHEALTHY, "test")
    for i in range(12, 16):
        await observe(detector, i, 0.1, generation=1)
    assert tracker.replicas["r"].lifecycle is ReplicaLifecycle.UNHEALTHY
    assert not await tracker.set_lifecycle(
        "r", ReplicaLifecycle.HEALTHY, "stale", expected=(ReplicaLifecycle.DEGRADED, 0, epoch)
    )


async def test_expiry_and_workload_filter():
    tracker, detector = setup()
    for i in range(3):
        await observe(detector, i, 0.5)
    await observe(detector, 20, 0.5)
    assert tracker.replicas["r"].lifecycle is ReplicaLifecycle.HEALTHY
    for i in range(21, 25):
        await detector.observe(
            "r",
            dispatch=i,
            first_content=i + 0.5,
            now=i + 0.6,
            generation=0,
            epoch=0,
            input_tokens=2,
            output_tokens=1,
            request_id=str(i),
        )
    assert not tracker.lifecycle_events


async def test_hysteresis_stale_observation_and_fresh_generation_recovery():
    tracker, detector = setup()
    for i in range(4):
        await observe(detector, i, 0.350)
    assert not tracker.lifecycle_events
    for i in range(4, 8):
        await observe(detector, i, 0.5)
    assert tracker.replicas["r"].lifecycle is ReplicaLifecycle.DEGRADED
    for i in range(8, 12):
        await observe(detector, i, 0.201)
    assert tracker.replicas["r"].lifecycle is ReplicaLifecycle.DEGRADED
    epoch = tracker.replicas["r"].lifecycle_epoch
    await detector.observe(
        "r",
        dispatch=0,
        first_content=0.1,
        now=30,
        generation=0,
        epoch=epoch,
        input_tokens=1024,
        output_tokens=128,
        request_id="stale",
    )
    assert detector.events[-1]["reason"] == "stale_observation"
    await tracker.new_generation("r")
    for i in range(31, 35):
        await observe(detector, i, 0.1, generation=1)
    assert tracker.replicas["r"].lifecycle is ReplicaLifecycle.HEALTHY


async def test_exact_recovery_threshold_holds():
    tracker, detector = setup()
    await tracker.set_lifecycle("r", ReplicaLifecycle.DEGRADED, "test")
    for i in range(4):
        await detector.observe(
            "r",
            dispatch=0.0,
            first_content=0.2,
            now=0.3 + i * 0.1,
            generation=0,
            epoch=1,
            input_tokens=1024,
            output_tokens=128,
            request_id=str(i),
        )
    assert tracker.replicas["r"].lifecycle is ReplicaLifecycle.DEGRADED
