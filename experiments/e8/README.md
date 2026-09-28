# E8 prediction-admission experiment

E8 is an offline-prepared, paired experiment asking one narrow question: does enforcing the frozen E7 count rule improve request-level SLO goodput relative to otherwise identical permissive admission? This directory contains the frozen protocol, arm catalog, harness, CPU preflight, and future operator commands. It does not contain GPU results.

Configuration A records the selected replica's count prediction and admits it subject only to the shared safety limits. Configuration B rejects with `429 prediction_count_threshold` when that same pre-admission count is at least 8. Selection, observation, decision, and reservation are atomic. A rejected round-robin turn is consumed; the gateway neither reroutes nor probes the other replica.

Run CPU-only checks from this directory with `uv run --project . --group dev pytest -q`. The GPU procedure is in `EXECUTION_COMMANDS.md`; its gates are normative. The completed bounded execution record is in [`artifacts/summaries/e8-execution-result.md`](../../artifacts/summaries/e8-execution-result.md).
