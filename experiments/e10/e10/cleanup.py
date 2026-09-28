from __future__ import annotations

import os
import signal
import time

from e10.injector import OwnedProcess, verify


def terminate_owned(item: OwnedProcess, *, timeout: float = 10.0) -> dict[str, object]:
    """Terminate only the exact registered process generation."""
    verify(item)
    os.killpg(item.pgid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(item.pid, 0)
        except ProcessLookupError:
            return {"event": "failure_cleanup", "pid": item.pid, "signal": "SIGTERM"}
        time.sleep(0.05)
    verify(item)
    os.killpg(item.pgid, signal.SIGKILL)
    return {"event": "failure_cleanup", "pid": item.pid, "signal": "SIGKILL"}
