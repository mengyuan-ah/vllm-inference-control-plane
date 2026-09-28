import json
import sys
from pathlib import Path

import pytest

import gpu_servers
import run_harness
from e11.protocol import CATALOG, SETTINGS
from qualify import check


def test_frozen_catalog_and_settings_files_match_code():
    root = Path(__file__).parents[1]
    assert json.loads((root / "ARM_CATALOG.json").read_text()) == CATALOG
    assert json.loads((root / "FROZEN_SETTINGS.json").read_text()) == SETTINGS
    assert [a["arm"] for a in CATALOG] == ["A", "B", "B", "A", "A", "B"]
    assert [a["target"] for a in CATALOG] == [
        "replica1",
        "replica1",
        "replica2",
        "replica2",
        "replica1",
        "replica1",
    ]


def test_missing_prerequisites_fail_closed(tmp_path):
    assert not check(tmp_path, "a" * 40)["valid"]


def test_gpu_paths_refuse_without_authorization(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gpu_servers.py", "start", "--root", str(tmp_path / "gpu")])
    with pytest.raises(SystemExit) as exc:
        gpu_servers.main()
    assert exc.value.code == 2 and not (tmp_path / "gpu").exists()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_harness.py",
            "--backend-urls",
            "http://unused",
            "http://unused",
            "--tokenizer",
            "unused",
            "--output",
            str(tmp_path / "arm"),
            "--source-commit",
            "a" * 40,
            "--arm-id",
            "r1-A",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        run_harness.main()
    assert exc.value.code == 2 and not (tmp_path / "arm").exists()
