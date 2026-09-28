# E11 future execution commands — NOT run during preparation

Separate explicit GPU authorization and review are required. Preparation ends after CPU
checks. Do not use the presence of GPUs as authorization. Never stop the pod automatically.

## CPU-only preparation / verification

From a clean reviewed checkout on `/workspace`:

```bash
cd "/workspace/project/experiments/e11"
export UV_CACHE_DIR=/workspace/project/uv-cache
export UV_PYTHON_INSTALL_DIR=/workspace/project/python
export TMPDIR=/workspace/project/tmp
mkdir -p "$TMPDIR"
uv sync --frozen
uv run pytest -q
uv run ruff check . ../../src/gateway
uv run ruff format --check . ../../src/gateway
uv run python -m compileall -q e11 . ../../src/gateway
# Choose a NEW output path; existing directories are intentionally rejected.
uv run python cpu_preflight.py --output /workspace/project/cpu-preflight-reviewed
```

Also run E10 and gateway regression suites in their own working directories, using the
E11 virtualenv interpreter and their own pytest import configuration. Do not set E11's
module path first when running E10: both have a `run_harness.py` by design.

## Future GPU qualification (separate authorization only)

Use the same pinned vLLM image/runtime as E10. Record package/runtime versions, GPU
identity, exact source SHA, expanded argv, model/tokenizer snapshot identity, and port
ownership before any launch. Compare model snapshot revision and request tokenization;
require two independent one-GPU replicas with identical launch settings.

Port8001 must remain untouched. Record all existing listeners and their process owners;
if8101/8102 already belong to any service, **stop and request direction**. Do not evict
existing vLLM processes. `gpu_servers.py` refuses occupied bind addresses and operates
only on its own verified registries, never on a process-name pattern.

```bash
# Execute only AFTER approval, from the clean reviewed E11 directory.
set -euo pipefail
export E11_SOURCE=$(git rev-parse HEAD)
export E11_RUN=/workspace/e11-results/e11-$(date -u +%Y%m%dT%H%M%SZ)-${E11_SOURCE:0:7}
export E11_MODEL=/workspace/models/Qwen--Qwen3-4B-Instruct-2507/cdbee75f17c01a7cc42f958dc650907174af0554
mkdir "$E11_RUN"
cp ARM_CATALOG.json FROZEN_SETTINGS.json FROZEN_PROTOCOL.md "$E11_RUN/"
uv run python gpu_servers.py start --authorize-gpu-inference \
  --root "$E11_RUN/owned-servers" --model "$E11_MODEL"
```

This starts only new task-owned backend processes and waits at most180s for health.
The launcher uses E10's verified process registration and pins GPU0/1 explicitly.
Check logs and both `/v1/models` responses before traffic. The recorded pinned-runtime
prefix-cache flag is `--no-enable-prefix-caching` (not an assumed alternative spelling).
Require clean health, correct model name/revision, no OOM, and no backend waiting.
If registration fails, preserve diagnostics and resolve any unregistered owned child by
verified identity; do not issue a broad kill. Do not continue qualification blindly.

## Fixed calibration, paired pilot, then measured catalog

```bash
mkdir "$E11_RUN/prerequisites" "$E11_RUN/measured"
# Exactly one fixed-rate single-backend calibration (no slowdown).
uv run python run_harness.py --authorize-gpu-inference --stage calibration \
  --arm-id r1-A --backend-urls http://127.0.0.1:8101 http://127.0.0.1:8101 \
  --tokenizer "$E11_MODEL" --source-commit "$E11_SOURCE" \
  --output "$E11_RUN/prerequisites/calibration"

for arm in A B; do
  uv run python run_harness.py --authorize-gpu-inference --stage pilot \
    --arm-id "r1-$arm" --backend-urls http://127.0.0.1:8101 http://127.0.0.1:8102 \
    --tokenizer "$E11_MODEL" --source-commit "$E11_SOURCE" \
    --prerequisite-root "$E11_RUN/prerequisites" \
    --output "$E11_RUN/prerequisites/pilot-$arm"
done
uv run python qualify.py "$E11_RUN/prerequisites" --source-commit "$E11_SOURCE" \
  --output "$E11_RUN/qualification.json"
# Nonzero exit STOPS here. No changed thresholds, rates, seeds, or automatic retries.

for arm in r1-A r1-B r2-B r2-A r3-A r3-B; do
  # Before each arm verify both owned process identities and healthy drained endpoints.
  PYTHONPATH=../e10 uv run python -c \
    'import os; from pathlib import Path; from e10.injector import load,verify; root=Path(os.environ["E11_RUN"])/"owned-servers"; [verify(load(root/name)) for name in ("replica1.json","replica2.json")]'
  uv run python run_harness.py --authorize-gpu-inference --stage measured \
    --arm-id "$arm" --backend-urls http://127.0.0.1:8101 http://127.0.0.1:8102 \
    --tokenizer "$E11_MODEL" --source-commit "$E11_SOURCE" \
    --prerequisite-root "$E11_RUN/prerequisites" --output "$E11_RUN/measured/$arm"
done
uv run python analyze.py "$E11_RUN/measured" --output "$E11_RUN/analysis.json"
```

A failed analysis is not repaired by deleting arms or changing thresholds. Preserve
partial evidence and report the failed gate. No measured campaign ran in preparation.

## Owned teardown and archival

Run teardown even after failed qualification/analysis, but only for matching task-owned
registries. This command does not stop the pod or any unrelated service:

```bash
uv run python gpu_servers.py stop --authorize-gpu-inference --root "$E11_RUN/owned-servers"
# Verify original port ownership (including8001) is unchanged and owned endpoints closed.
# Finalize only after log writers and task-owned services have closed.
(cd "$E11_RUN" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
tar -C "$(dirname "$E11_RUN")" -czf "$E11_RUN.tar.gz" "$(basename "$E11_RUN")"
sha256sum "$E11_RUN.tar.gz" > "$E11_RUN.tar.gz.sha256"
```

Keep the archive and checksum on the network volume; arrange any independent backup
only with separate authorization. Historical E10/E9 evidence is never rewritten.
