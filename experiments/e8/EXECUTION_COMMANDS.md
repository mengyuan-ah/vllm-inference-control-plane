# Future E8 execution commands

These are operator-reviewed future commands, not evidence that E8 ran. Do not execute measured arms before smoke and pilot pass and a human explicitly approves GPU use.

## Qualification and setup

Use a Runpod pod with the persistent network volume mounted at `/workspace`, exactly two RTX 4090 GPUs, and the repository checkout at the reviewed E8 commit. Before traffic, capture `git rev-parse HEAD`, `nvidia-smi -L`, `python --version`, `python -m pip freeze`, disk free space, and the model snapshot commit in `$RUN_ROOT/qualification/`. Verify two GPUs, vLLM `0.25.1+cu129`, model revision `cdbee75f17c01a7cc42f958dc650907174af0554`, and at least 20 GiB free on the network volume. Stop on any mismatch.

```bash
cd '/workspace/project/experiments/e8'
export RUN_ID="e8-$(date -u +%Y%m%dT%H%M%SZ)"
export RUN_ROOT="/workspace/e8-results/$RUN_ID"
mkdir -p "$RUN_ROOT/qualification"
git rev-parse HEAD > "$RUN_ROOT/qualification/git-head.txt"
nvidia-smi -L > "$RUN_ROOT/qualification/gpus.txt"
python --version > "$RUN_ROOT/qualification/python.txt" 2>&1
python -m pip freeze > "$RUN_ROOT/qualification/pip-freeze.txt"
df -h /workspace > "$RUN_ROOT/qualification/disk.txt"
```

Start one vLLM process per GPU on fixed ports. The explicit `--no-enable-prefix-caching`
flags are mandatory; TP and PP are one. Record the PIDs so teardown targets only these owned
processes.

```bash
mkdir -p "$RUN_ROOT/processes" "$RUN_ROOT/logs"
nohup env CUDA_VISIBLE_DEVICES=0 vllm serve Qwen/Qwen3-4B-Instruct-2507 --revision cdbee75f17c01a7cc42f958dc650907174af0554 --host 127.0.0.1 --port 8101 --tensor-parallel-size 1 --pipeline-parallel-size 1 --no-enable-prefix-caching --disable-log-requests > "$RUN_ROOT/logs/replica1.log" 2>&1 &
echo $! > "$RUN_ROOT/processes/replica1.pid"
nohup env CUDA_VISIBLE_DEVICES=1 vllm serve Qwen/Qwen3-4B-Instruct-2507 --revision cdbee75f17c01a7cc42f958dc650907174af0554 --host 127.0.0.1 --port 8102 --tensor-parallel-size 1 --pipeline-parallel-size 1 --no-enable-prefix-caching --disable-log-requests > "$RUN_ROOT/logs/replica2.log" 2>&1 &
echo $! > "$RUN_ROOT/processes/replica2.pid"
```

In a third shell, set `TOKENIZER_SNAPSHOT` to the exact local revision directory. Confirm both `/health` and `/metrics` endpoints. Send separate warmup requests to each backend and wait until both `vllm:num_requests_running` and `vllm:num_requests_waiting` are zero. Warmup output goes under `$RUN_ROOT/warmup`, never an arm directory.

## Smoke and pilot gates

Run the CPU preflight first; its create-once output protects against accidental reuse.

```bash
uv run --project . --group dev pytest -q
uv run --project . python cpu_preflight.py --output-root "$RUN_ROOT/cpu-preflight"
uv run --project . python run_harness.py --configuration A --stage smoke --rate 11 --count 16 --seed 80001 --backend-url http://127.0.0.1:8101 --backend-url http://127.0.0.1:8102 --tokenizer "$TOKENIZER_SNAPSHOT" --output-root "$RUN_ROOT"
uv run --project . python run_harness.py --configuration B --stage smoke --rate 11 --count 16 --seed 80001 --backend-url http://127.0.0.1:8101 --backend-url http://127.0.0.1:8102 --tokenizer "$TOKENIZER_SNAPSHOT" --output-root "$RUN_ROOT"
```

The CPU preflight is the bounded rejection-verification gate: it deliberately holds one request
per replica and proves that the next selected request is rejected without reservation or backend
dispatch. The 16-offer GPU smoke is only a serving, telemetry, paired-trace, and accounting check;
it is not required to reach count eight. Smoke passes only if the CPU preflight passes, both GPU
manifests are valid, traces match, all outcome/accounting checks reconcile, and both backends
dispatch. Otherwise stop and preserve the run.

Pilot is capped at four arms and ten minutes overall: rate 9 seed 81001 in order A/B, then rate 10 seed 81002 in order B/A, 64 offers each. Use the same command shape with `--stage pilot --count 64`. Pilot passes only if all four arms are valid, paired traces match, B exercises rejection in at least one arm, no safety limit binds A, and no threshold or protocol change is proposed. Any required change creates a new protocol commit; do not proceed to measurement.

## Measured execution

After explicit approval, execute the 18 arms in `ARM_CATALOG.json` order, substituting configuration/rate/seed below. Drain both replicas and verify zero gateway/backend work between arms. Never retry requests or reuse an arm directory.

```bash
uv run --project . python run_harness.py --configuration CONFIG --stage measured --rate RATE --count 256 --seed SEED --backend-url http://127.0.0.1:8101 --backend-url http://127.0.0.1:8102 --tokenizer "$TOKENIZER_SNAPSHOT" --output-root "$RUN_ROOT"
uv run --project . python analyze.py "$RUN_ROOT"
```

## Preserve, copy off-pod, and teardown

Inventory and archive all manifests, request rows, traces, qualification data, logs, and analysis on the network volume. Copy the archive off-pod before pod deletion; stopping a pod preserves a network volume but container-disk-only files are not the evidence source of record.

```bash
find "$RUN_ROOT" -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 shasum -a 256 > "$RUN_ROOT/SHA256SUMS"
tar -C /workspace/e8-results -czf "/workspace/e8-results/$RUN_ID.tar.gz" "$RUN_ID"
```

From the operator workstation, retrieve `/workspace/e8-results/$RUN_ID.tar.gz` with the approved Runpod transfer workflow (for example `runpodctl receive`) or upload it to the approved object store, verify its SHA-256 locally, then stop the pod. Delete neither the network-volume run directory nor its archive until review accepts the off-pod copy.

After the archive is verified off-pod, stop only the two recorded processes. Each PID must still
identify the expected E8 vLLM command; abort teardown on a mismatch.

```bash
for PID_FILE in "$RUN_ROOT/processes/replica1.pid" "$RUN_ROOT/processes/replica2.pid"; do
  PID="$(cat "$PID_FILE")"
  COMMAND="$(ps -p "$PID" -o command=)"
  case "$COMMAND" in
    *"vllm serve Qwen/Qwen3-4B-Instruct-2507"*) kill "$PID" ;;
    *) echo "refusing to stop unowned PID $PID: $COMMAND" >&2; exit 1 ;;
  esac
done
```

Confirm both PIDs have exited and both ports are closed before stopping or terminating the pod.
The evidence remains on the independent network volume until review authorizes its removal.
