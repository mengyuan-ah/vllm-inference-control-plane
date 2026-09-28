# E10 future GPU execution commands

These commands are a proposal, not authorization to execute E10. Run qualification, smoke,
pilot, and six measured arms only after review. Use a clean detached checkout of the reviewed
commit and task-specific `UV_CACHE_DIR`; do not use `--disable-log-requests`.

Both servers must include `--disable-prefix-caching`, `--tensor-parallel-size 1`,
`--pipeline-parallel-size 1`, `--max-model-len 8192`, `--seed 42`, and the frozen model/revision.
Capture the exact expanded argv, PID/PGID, GPU, ports, `/proc` identity, and process generation.

```bash
export E10_ROOT=/workspace/e10-results/e10-$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short HEAD)
export UV_CACHE_DIR=/workspace/project/cache/uv
mkdir "$E10_ROOT"                         # collision is an error
uv sync --frozen --all-groups
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
python -m compileall -q e10 ../../src/gateway

# CPU preflight only (safe on a CPU pod)
uv run python cpu_preflight.py --output "$E10_ROOT/cpu-preflight"

# Future sequence after GPU authorization:
# 1 qualification; 2 GPU smoke; 3 one-replica pilot; 4 freeze selected rate/catalog;
# 5 six catalog-ordered arms; 6 restore both owned replicas and verify; 7 stop only owned jobs.

# Finalize only after owned processes/logs are closed. Inventory excludes itself.
(cd "$E10_ROOT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
tar -C "$(dirname "$E10_ROOT")" -czf "$E10_ROOT.tar.gz" "$(basename "$E10_ROOT")"
sha256sum "$E10_ROOT.tar.gz" > "$E10_ROOT.tar.gz.sha256"
# Copy the finalized archive + detached checksum to an independent backup location/account.
```

Teardown must load the experiment registry, verify `/proc` identity for every generation, and
signal only still-matching process groups. Never use `pkill`, `killall`, or a name pattern.
