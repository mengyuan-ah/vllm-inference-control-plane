from __future__ import annotations

import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).parents[1]))

from gateway.app import Gateway, create_app  # noqa: E402
from gateway.config import GatewayConfig, ReplicaConfig  # noqa: E402
from tests.fake_backend import FakeBackend  # noqa: E402


class FixedCounter:
    def count(self, _prompt: str) -> int:
        return 2


@pytest.fixture
async def integration_stack():
    fake1 = FakeBackend("replica1")
    fake2 = FakeBackend("replica2")
    server1 = TestServer(fake1.app())
    server2 = TestServer(fake2.app())
    await server1.start_server()
    await server2.start_server()
    config = GatewayConfig(
        replicas=(
            ReplicaConfig("replica1", str(server1.make_url("")).rstrip("/"), max_inflight=1),
            ReplicaConfig("replica2", str(server2.make_url("")).rstrip("/"), max_inflight=1),
        ),
        max_queued_requests=4,
        max_queued_work=100,
        max_queue_residence=0.5,
    )
    gateway = Gateway(config, token_counter=FixedCounter())
    gateway_server = TestServer(create_app(gateway))
    client = TestClient(gateway_server)
    await client.start_server()
    try:
        yield client, gateway, fake1, fake2
    finally:
        await client.close()
        await server1.close()
        await server2.close()
