import json

import analyze as analysis_module
from analyze import analyze, arm_result
from cpu_preflight import run
from e11.protocol import trace_hash


async def test_real_fake_pair_and_corrupted_evidence(tmp_path, monkeypatch):
    root = tmp_path / "fake"
    result = await run(root)
    assert result["valid"]
    assert result["B"]["phase_dispatch"]["slow"]["target_fraction"] < 0.4
    assert result["A"]["transitions"] == 0
    assert result["B"]["transitions"] == 2
    lifecycle_path = root / "cpu-B" / "lifecycle.json"
    original = lifecycle_path.read_text()
    events = json.loads(original)
    manifest = json.loads((root / "cpu-B" / "manifest.json").read_text())
    for event in events:
        if event["to"] == "Healthy":
            event["timestamp"] = (
                manifest["start_monotonic"] + manifest["settings"]["offer_window_s"] + 1
            )
    lifecycle_path.write_text(json.dumps(events))
    censored = arm_result(root / "cpu-B")
    assert censored["recovery_censored"] and censored["recovery_delay_s"] is None
    lifecycle_path.write_text(original)
    verify_campaign_fixture(root, tmp_path / "campaign", monkeypatch)
    path = root / "cpu-B" / "requests.json"
    rows = json.loads(path.read_text())
    rows[0]["prompt"] = "tampered"
    path.write_text(json.dumps(rows))
    assert "request_content_hash" in arm_result(root / "cpu-B")["failures"]
    assert not analyze(root)["valid"]


def test_empty_campaign_invalid(tmp_path):
    result = analyze(tmp_path)
    assert not result["valid"] and not result["pairs"]


def verify_campaign_fixture(root, campaign, monkeypatch):
    """Expand the real fake pair into a time-shifted six-arm validation fixture."""
    catalog = []
    settings = json.loads((root / "cpu-A" / "manifest.json").read_text())["settings"]
    for order, (rep, arm) in enumerate(
        [(1, "A"), (1, "B"), (2, "B"), (2, "A"), (3, "A"), (3, "B")]
    ):
        spec = dict(
            arm_id=f"r{rep}-{arm}",
            pair_id=f"r{rep}",
            arm=arm,
            seed=119001,
            target="replica1",
            order=order,
        )
        catalog.append(spec)
        dest = campaign / spec["arm_id"]
        dest.mkdir(parents=True)

        def shift(value, order=order, rep=rep):
            if isinstance(value, list):
                return [shift(v) for v in value]
            if isinstance(value, dict):
                result = {k: shift(v) for k, v in value.items()}
                for key in (
                    "timestamp",
                    "start_monotonic",
                    "end_monotonic",
                    "received",
                    "scheduled",
                    "actual_send_monotonic",
                    "completion_monotonic",
                    "dispatch",
                    "first_content",
                ):
                    if isinstance(result.get(key), (float, int)):
                        result[key] += order * 100
                if isinstance(result.get("request_id"), str):
                    result["request_id"] = result["request_id"].replace("cpu-", f"r{rep}-")
                return result
            return value

        for file in (root / f"cpu-{arm}").glob("*.json"):
            (dest / file.name).write_text(json.dumps(shift(json.loads(file.read_text()))))
        m = json.loads((dest / "manifest.json").read_text())
        m.update(spec, stage="measured")
        rows = json.loads((dest / "requests.json").read_text())
        m["trace_sha256"] = trace_hash(rows)
        (dest / "manifest.json").write_text(json.dumps(m))
    with monkeypatch.context() as patch:
        patch.setattr(analysis_module, "CATALOG", catalog)
        patch.setattr(analysis_module, "SETTINGS", settings)
        result = analyze(campaign)
        assert result["valid"], result["failures"]
        assert len(result["pairs"]) == 3
        mpath = campaign / "r2-A" / "manifest.json"
        m = json.loads(mpath.read_text())
        m["source_commit"] = "different-source"
        mpath.write_text(json.dumps(m))
        result = analyze(campaign)
        assert not result["valid"] and not result["pairs"]
        assert "source_identity_mismatch" in result["failures"]
