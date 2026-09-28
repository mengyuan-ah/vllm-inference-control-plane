import asyncio
import json
import os
import signal
import sys
import time
from argparse import Namespace
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import run_harness
from analyze import analyze
from e10 import injector
from e10.runtime import (
    INPUT_TOKENS,
    RECOVERY_MAX_WORK,
    REQUEST_WORK,
    StreamObservation,
    build_exact_prompt,
    derived_offer_count,
)


class CharacterTokenizer:
    def encode(self, text, add_special_tokens=False):
        return [ord(char) for char in text]

    def decode(self, token_ids, skip_special_tokens=False):
        return "".join(chr(token) for token in token_ids)


def test_restart_survives_event_loop_close_and_binds_gpu(tmp_path):
    command = [
        sys.executable,
        "-u",
        "-c",
        "import os,time; print(os.environ['CUDA_VISIBLE_DEVICES'], flush=True); time.sleep(30)",
    ]
    log = tmp_path / "restart.log"

    async def arm():
        child = injector.launch_restart(command, "1", log)
        for _ in range(100):
            if log.read_text().strip():
                return child
            await asyncio.sleep(0.01)
        raise AssertionError("child did not start")

    process = asyncio.run(arm())
    try:
        time.sleep(0.05)
        assert process.poll() is None
        assert log.read_text().strip() == "1"
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)


def test_real_request_fits_recovery_headroom():
    assert REQUEST_WORK == pytest.approx(14756.0981376)
    assert RECOVERY_MAX_WORK >= REQUEST_WORK


def test_exact_prompt_is_measured_not_hardcoded():
    prompt, token_ids = build_exact_prompt(CharacterTokenizer(), 101001, 3)
    assert len(token_ids) == INPUT_TOKENS
    assert len(CharacterTokenizer().encode(prompt)) == INPUT_TOKENS


def test_offer_count_is_derived_from_fixed_window():
    assert derived_offer_count(2.0, 60.0, None) == 120
    with pytest.raises(ValueError, match="conflicts"):
        derived_offer_count(2.0, 60.0, 240)


def test_stream_observation_computes_ttft_tpot_and_slo():
    observation = StreamObservation()
    observation.feed(json.dumps({"choices": [{"text": "x", "finish_reason": None}]}), 10.1)
    observation.feed(
        json.dumps(
            {
                "choices": [{"text": "", "finish_reason": "length"}],
                "usage": {"completion_tokens": 128},
            }
        ),
        10.2,
    )
    observation.feed("[DONE]", 10.2)
    metrics = observation.metrics(10.0, 10.2)
    assert metrics["success"]
    assert metrics["ttft_s"] == pytest.approx(0.1)
    assert metrics["tpot_s"] == pytest.approx(0.1 / 127)
    assert metrics["slo_success"]


def test_empty_and_incomplete_evidence_are_invalid(tmp_path: Path):
    empty = analyze(tmp_path)
    assert not empty["valid"]
    assert "expected exactly 6 measured arms, found 0" in empty["failures"]
    arm = tmp_path / "arm-control"
    arm.mkdir()
    (arm / "manifest.json").write_text(
        json.dumps(
            {
                "arm_id": "arm-control",
                "pair_id": "pair-1",
                "condition": "control",
                "offered": 1,
                "final_accounting": {},
            }
        )
    )
    (arm / "requests.jsonl").write_text("")
    incomplete = analyze(tmp_path)
    assert not incomplete["valid"]
    assert any("incomplete pair" in failure for failure in incomplete["failures"])


@pytest.mark.asyncio
async def test_restart_registration_waits_for_exec_and_records_command(tmp_path: Path, monkeypatch):
    command = [
        "env",
        "E10_REGISTRATION_TEST=1",
        "/usr/bin/python3",
        "-c",
        "import time; time.sleep(30)",
    ]
    expected = command[2:]
    identities = iter(
        [
            (1234, 99, ["parent-before-exec"]),
            (1234, 99, expected),
            (1234, 99, expected),
        ]
    )
    monkeypatch.setattr(injector, "_identity", lambda _pid: next(identities))
    registry = tmp_path / "owned.json"
    item = await injector.register_after_exec(
        registry,
        "replica1",
        1234,
        "0",
        [8101],
        2,
        command,
        poll_interval=0,
    )
    assert item.command == expected
    assert item.command
    assert injector.load(registry) == item


@pytest.mark.asyncio
async def test_actual_harness_records_streaming_slo_and_realistic_work(tmp_path, monkeypatch):
    async def health(_request):
        return web.Response(status=200)

    async def complete(request):
        first = {"choices": [{"text": "x", "finish_reason": None}]}
        final = {
            "choices": [{"text": "", "finish_reason": "length"}],
            "usage": {"prompt_tokens": 1024, "completion_tokens": 128},
        }
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for event in (json.dumps(first), json.dumps(final), "[DONE]"):
            await response.write(f"data: {event}\n\n".encode())
            await asyncio.sleep(0.001)
        await response.write_eof()
        return response

    def backend_app():
        app = web.Application()
        app.router.add_get("/health", health)
        app.router.add_post("/v1/completions", complete)
        return app

    left = TestServer(backend_app())
    right = TestServer(backend_app())
    await left.start_server()
    await right.start_server()

    class Counter:
        def __init__(self, _path):
            pass

        def count(self, prompt):
            return len(prompt)

    monkeypatch.setattr(run_harness, "HuggingFaceTokenCounter", Counter)
    args = Namespace(
        condition="control",
        replica1_url=str(left.make_url("")).rstrip("/"),
        replica2_url=str(right.make_url("")).rstrip("/"),
        tokenizer=tmp_path,
        output=tmp_path / "arm",
        arm_id="arm",
        pair_id="pair",
        source_commit="test",
        seed=101001,
        rate=5.0,
        offers=None,
        offer_window=0.2,
        fault_at=0.01,
        restart_at=0.02,
        client_timeout=2.0,
        drain_timeout=2.0,
        registry=None,
        restart_registry=None,
        restart_log=None,
        restart_generation=None,
        restart_command_json=None,
        target="replica1",
        gpu="0",
        ports=[8101],
    )
    try:
        manifest = await run_harness.run(args, tokenizer=CharacterTokenizer())
    finally:
        await left.close()
        await right.close()
    rows = [json.loads(line) for line in (args.output / "requests.jsonl").read_text().splitlines()]
    assert manifest["offered"] == 1
    assert manifest["request_work"] == pytest.approx(REQUEST_WORK)
    assert rows[0]["input_tokens"] == 1024
    assert rows[0]["success"]
    assert rows[0]["ttft_s"] is not None
    assert rows[0]["tpot_s"] is not None
