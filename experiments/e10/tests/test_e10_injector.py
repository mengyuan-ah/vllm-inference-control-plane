import asyncio
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from e10.injector import hard_fail, register_after_exec, verify


@pytest.mark.skipif(
    not Path("/proc/self/stat").exists(),
    reason="owned-process identity checks require Linux /proc",
)
def test_owned_process_kill_and_mismatch_refusal(tmp_path):
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        item = asyncio.run(
            register_after_exec(
                tmp_path / "owned.json",
                "replica1",
                process.pid,
                "fake",
                [1234],
                1,
                [sys.executable, "-c", "import time; time.sleep(30)"],
            )
        )
        verify(item)
        with pytest.raises(RuntimeError, match="identity mismatch"):
            verify(replace(item, start_ticks=item.start_ticks + 1))
        event = hard_fail(item)
        assert event["signal"] == "SIGKILL"
        assert process.wait(timeout=2) < 0
    finally:
        if process.poll() is None:
            os.killpg(process.pid, 9)
