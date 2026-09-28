from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class OwnedProcess:
    replica_id: str
    pid: int
    pgid: int
    start_ticks: int
    command: list[str]
    gpu: str
    ports: list[int]
    generation: int


def _identity(pid: int) -> tuple[int, int, list[str]]:
    stat = Path(f"/proc/{pid}/stat").read_text().split()
    pgid = int(stat[4])
    start_ticks = int(stat[21])
    command = [
        part.decode() for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if part
    ]
    return pgid, start_ticks, command


def register(
    path: Path, replica_id: str, pid: int, gpu: str, ports: list[int], generation: int
) -> OwnedProcess:
    pgid, start_ticks, command = _identity(pid)
    if pgid != pid:
        raise ValueError("owned replica must be a process-group leader")
    item = OwnedProcess(replica_id, pid, pgid, start_ticks, command, gpu, ports, generation)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(item), indent=2, sort_keys=True) + "\n")
    return item


def exec_command(command: list[str]) -> list[str]:
    """Return the argv expected in /proc after an optional `env` wrapper execs."""
    if not command or Path(command[0]).name != "env":
        return command
    index = 1
    while index < len(command) and "=" in command[index]:
        index += 1
    return command[index:]


async def register_after_exec(
    path: Path,
    replica_id: str,
    pid: int,
    gpu: str,
    ports: list[int],
    generation: int,
    launch_command: list[str],
    *,
    identity_timeout: float = 5.0,
    poll_interval: float = 0.01,
) -> OwnedProcess:
    """Wait for exec completion before persisting an owned-process identity."""
    expected = exec_command(launch_command)
    deadline = time.monotonic() + identity_timeout
    last_command: list[str] = []
    while time.monotonic() < deadline:
        try:
            _, _, last_command = _identity(pid)
        except FileNotFoundError:
            raise RuntimeError("restarted process exited before identity registration") from None
        if last_command == expected:
            return register(path, replica_id, pid, gpu, ports, generation)
        await asyncio.sleep(poll_interval)
    raise RuntimeError(
        "restarted process did not reach expected exec identity; "
        f"expected={expected!r}, observed={last_command!r}"
    )


def load(path: Path) -> OwnedProcess:
    return OwnedProcess(**json.loads(path.read_text()))


def launch_restart(command: list[str], gpu: str, log_path: Path) -> subprocess.Popen:
    """Keep the replica alive beyond an arm's asyncio loop and preserve GPU binding."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as output:
        return subprocess.Popen(
            exec_command(command),
            env={**os.environ, "CUDA_VISIBLE_DEVICES": gpu},
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
        )


def verify(item: OwnedProcess) -> None:
    pgid, start_ticks, command = _identity(item.pid)
    if (pgid, start_ticks, command) != (item.pgid, item.start_ticks, item.command):
        raise RuntimeError("owned process identity mismatch; refusing signal")


def hard_fail(item: OwnedProcess, *, now=time.monotonic) -> dict[str, object]:
    verify(item)
    actual = now()
    os.killpg(item.pgid, signal.SIGKILL)
    return {
        "event": "hard_failure_signal",
        "replica_id": item.replica_id,
        "generation": item.generation,
        "signal": "SIGKILL",
        "actual_signal_monotonic": actual,
        "pid": item.pid,
        "pgid": item.pgid,
    }
