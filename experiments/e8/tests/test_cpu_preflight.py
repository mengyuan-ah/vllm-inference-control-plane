from __future__ import annotations

import pytest

from cpu_preflight import main


@pytest.mark.asyncio
async def test_fake_stack_exercises_both_configs_and_collision_guard(tmp_path) -> None:
    output = tmp_path / "evidence"
    await main(output)
    assert (output / "preflight.json").is_file()
    with pytest.raises(FileExistsError):
        await main(output)
