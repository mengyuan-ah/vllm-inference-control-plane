from __future__ import annotations

import json

import pytest

from cpu_preflight import main


@pytest.mark.asyncio
async def test_fake_http_stack_exercises_exact_threshold_and_collision_guard(tmp_path) -> None:
    output = tmp_path / "preflight"
    await main(output)
    results = json.loads((output / "preflight.json").read_text())
    assert results[0]["configuration"] == "A"
    assert results[0]["backend_dispatches"] == 17
    assert results[1]["configuration"] == "B"
    assert results[1]["backend_dispatches"] == 16
    assert results[1]["final_status"] == 429
    with pytest.raises(FileExistsError):
        await main(output)
