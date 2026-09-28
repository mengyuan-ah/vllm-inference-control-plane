from __future__ import annotations

import json
from pathlib import Path

import pytest
from gateway.app import Gateway

import pilot_harness
from e5.preflight import EXPECTED_BETA, PILOT_COUNT, initialize_metadata
from e5.trace import FROZEN_BETA, PILOT_SEEDS, generate_trace


def test_imports_resolve_to_committed_e5_and_gateway() -> None:
    e5_root = Path(__file__).resolve().parents[1]
    repository_root = e5_root.parents[1]
    assert Path(pilot_harness.__file__).resolve() == e5_root / "pilot_harness.py"
    assert Path(Gateway.__module__.replace(".", "/") + ".py").parts[-2:] == (
        "gateway",
        "app.py",
    )
    assert (repository_root / "src/gateway/app.py").is_file()


def test_frozen_protocol_constants_and_seed_boundary() -> None:
    assert FROZEN_BETA == EXPECTED_BETA == 107.2820167
    assert PILOT_COUNT == 64
    assert PILOT_SEEDS == {51001, 51002, 51003, 51004}
    assert (
        len(generate_trace("mixed", request_count=PILOT_COUNT, rate=2.5, seed=51001, stage="pilot"))
        == PILOT_COUNT
    )
    with pytest.raises(ValueError, match="not reserved for pilot"):
        generate_trace("mixed", request_count=PILOT_COUNT, rate=2.5, seed=52001, stage="pilot")


def test_metadata_initialization_and_collision(tmp_path: Path) -> None:
    tokenizer = tmp_path / "tokenizer"
    tokenizer.mkdir()
    output = tmp_path / "evidence"
    metadata_path = initialize_metadata(output, tokenizer)
    metadata = json.loads(metadata_path.read_text())
    assert metadata["beta"] == EXPECTED_BETA
    assert metadata["pilot_count"] == PILOT_COUNT
    assert metadata["pilot_seeds"] == sorted(PILOT_SEEDS)
    with pytest.raises(FileExistsError):
        initialize_metadata(output, tokenizer)
