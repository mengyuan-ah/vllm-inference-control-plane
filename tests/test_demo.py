from __future__ import annotations

import io

import pytest

from demo.run_demo import run_demo


@pytest.mark.asyncio
async def test_demo_exercises_three_control_plane_scenarios() -> None:
    output = io.StringIO()

    await run_demo(output)

    text = output.getvalue()
    assert "[1/3] Work-aware routing" in text
    assert "work-aware selected replica-2" in text
    assert "[2/3] Overload protection" in text
    assert "admitted:  4" in text
    assert "rejected:  4" in text
    assert "[3/3] Streaming failover" in text
    assert 'client received: "hello world"' in text
    assert "retries after client-visible content: 0" in text
    assert "active_reservations: 0" in text
    assert text.rstrip().endswith("Demo complete.")
