# E9 future execution commands

These commands are an operator runbook, not authorization to run E9. Replace
`RUN_ROOT` with a fresh directory on persistent storage. Fail closed on every
qualification, smoke, pilot, drain, identity, or validity check.

## Qualification and CPU preflight

    cd "/workspace/project/experiments/e9"
    export UV_CACHE_DIR=/workspace/project/cache/uv
    uv run --project . --group dev pytest -q
    uv run --project . --group dev ruff check .
    test "$(git rev-parse HEAD)" = "<reviewed-e9-commit>"
    nvidia-smi --query-gpu=index,name,uuid --format=csv,noheader
    findmnt -T /workspace
    sha256sum ARM_CATALOG.json
    uv run --project . python cpu_preflight.py --output-root "$RUN_ROOT/cpu-preflight"

Qualification must confirm exactly two intended GPUs, the exact frozen model
snapshot, Python/dependency versions, and sufficient space. Preserve all output.

## Servers

Launch exactly one owned vLLM process per intended GPU, bound to localhost ports
8101 and 8102, with the frozen snapshot, `--max-model-len 8192`, TP=1, PP=1,
seed 42, and explicit `--no-enable-prefix-caching`. Record commands, environment,
PIDs, process start times, logs, and metrics. Validate PID identity before any
later signal. Do not use broad process-kill commands.

Launch commands must omit `--disable-log-requests` because the qualified E8
vLLM build rejected that option before traffic. No other E8 launch setting may
change.

## Warmup, smoke, and pilot

Warm both replicas separately and require health, successful streaming
completion, correct usage/finish reason, and zero gateway/backend drain before
each arm. Smoke all four condition identities with seed 90001. Pilot one full
four-condition repetition with seed 91001. The CPU preflight is the deterministic
threshold-rejection gate; GPU smoke need not force rejection.

Do not authorize measured execution unless all arms are valid, A/B scheduled
hashes match, all four content hashes match, both replicas receive dispatches,
arrival lag is within frozen bounds, metrics are present and finite, and final
accounting drains to zero.

## Measured order

Use `ARM_CATALOG.json` verbatim. For each of 12 arms pass the one-based catalog
position as `--arm-order-index` and the frozen catalog SHA-256 as
`--catalog-sha256`. The common harness form is:

    uv run --project . python run_harness.py \
      --stage measured --traffic <smooth|bursty> --configuration <A|B> \
      --count 256 --seed <92001|92002|92003> \
      --arm-order-index <1..12> --catalog-sha256 "$CATALOG_SHA" \
      --backend-url http://127.0.0.1:8101 \
      --backend-url http://127.0.0.1:8102 \
      --tokenizer "$MODEL_SNAPSHOT" --output-root "$RUN_ROOT"

Wait for both backend running/waiting metrics and gateway accounting to drain
before every arm. Stop on any nonzero harness exit. Preserve partial arm output.

After all arms:

    uv run --project . python analyze.py "$RUN_ROOT" --catalog ARM_CATALOG.json

The analysis must be valid before interpretation.

## Teardown and inventory

Signal only recorded owned PIDs after revalidating command, start time, ports,
and model identity. Wait and verify ports 8101/8102 are closed. Do not stop the
pod automatically.

Create a self-excluding inventory from inside `RUN_ROOT`:

    find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS

Archive only after processes are stopped and evidence is complete. Store archive
and checksum on persistent network storage. A network-volume archive is not an
independent backup.
