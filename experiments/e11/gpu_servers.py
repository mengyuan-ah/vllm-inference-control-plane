"""Future authorized execution only; never used by CPU preparation."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
from pathlib import Path

import aiohttp

sys.path.append(str(Path(__file__).resolve().parents[1] / "e10"))
from e10.cleanup import terminate_owned  # noqa: E402
from e10.injector import launch_restart, load, register_after_exec  # noqa: E402

from e11.protocol import SETTINGS  # noqa: E402


async def start(root, model):
    # Check both ports before starting anything; never evict an existing owner.
    sockets = []
    try:
        for port in (8101, 8102):
            sock = socket.socket()
            sockets.append(sock)
            sock.bind(("127.0.0.1", port))
    finally:
        for sock in sockets:
            sock.close()
    root.mkdir(parents=True, exist_ok=False)
    records = []
    try:
        for gpu, port in enumerate((8101, 8102)):
            command = [
                "/usr/bin/python3",
                "-m",
                "vllm.entrypoints.openai.api_server",
                "--model",
                str(model),
                "--tokenizer",
                str(model),
                "--served-model-name",
                SETTINGS["model"],
                "--revision",
                SETTINGS["revision"],
                "--tokenizer-revision",
                SETTINGS["revision"],
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--tensor-parallel-size",
                "1",
                "--pipeline-parallel-size",
                "1",
                "--max-model-len",
                "8192",
                "--seed",
                "42",
                "--no-enable-prefix-caching",
            ]
            process = launch_restart(command, str(gpu), root / f"replica{gpu + 1}.log")
            # A failed registration deliberately does not signal an unverified PID.
            item = await register_after_exec(
                root / f"replica{gpu + 1}.json",
                f"replica{gpu + 1}",
                process.pid,
                str(gpu),
                [port],
                1,
                command,
            )
            records.append(item)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1)) as session:
            async with asyncio.timeout(180):
                for port in (8101, 8102):
                    while True:
                        try:
                            async with session.get(f"http://127.0.0.1:{port}/health") as response:
                                if response.status == 200:
                                    break
                        except (aiohttp.ClientError, TimeoutError):
                            pass
                        await asyncio.sleep(0.5)
        (root / "ready.json").write_text(
            json.dumps(dict(ready=True, model=SETTINGS["model"], revision=SETTINGS["revision"]))
        )
    except BaseException:
        for item in records:
            terminate_owned(item)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop"))
    parser.add_argument("--authorize-gpu-inference", action="store_true")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    args = parser.parse_args()
    if not args.authorize_gpu_inference:
        parser.error("not authorized by the CPU preparation task")
    if args.action == "start":
        if args.model is None or args.model.resolve().name != SETTINGS["revision"]:
            parser.error("use the verified existing revision snapshot; no model downloads")
        asyncio.run(start(args.root, args.model.resolve()))
    else:
        for name in ("replica1.json", "replica2.json"):
            print(json.dumps(terminate_owned(load(args.root / name))))


if __name__ == "__main__":
    main()
