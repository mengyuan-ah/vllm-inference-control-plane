# E5 offline execution package

This package prepares, but does not execute, the E5 comparison of healthy round-robin and
frozen-beta work-aware routing. It is based on E4 merge `473b4b1` and freezes
`estimated_work = input_tokens + 107.2820167 * requested_output_tokens`.

The gateway is configured with `GATEWAY_ROUTING_MODE=round_robin|work_aware` and
`GATEWAY_WORK_ALPHA=1`, `GATEWAY_WORK_BETA=107.2820167`. Both replicas must use equal
`routing_capacity` values. E4 did not establish safety limits, so future pilot limits must be
permissive, identical across paired arms, and recorded as pilot choices rather than E4 findings.

## Paired traces

Generate each trace once with `e5.trace.generate_trace`; persist the canonical JSON and its
SHA-256 from `paired_arm_manifest`. Both policies consume that same file, preserving arrivals,
class order, prompt seeds, and request IDs. Homogeneous traces use `1024/128`. Mixed traces use
exactly 50% `1024/128`, 25% `4096/64`, and 25% `256/512`. Pilot seeds (`51001`-`51004`) and
measured seeds (`52001`-`52003`) are disjoint and enforced by code.

## Future reviewed GPU pilot

The mixed screen in `config/pilot.json` treats 2.5, 3.0, and 3.5 req/s as candidates and 4.0
req/s as conditional. It will select low, knee, and safe near-boundary points plus identical
safety limits. Pilot observations are excluded from the measured policy result.

## Future measured run

`config/measured-template.json` intentionally leaves all pilot-dependent loads unresolved. Do
not replace them until the pilot is reviewed. The template fixes two policies, three paired
repetitions, 128 requests per homogeneous arm, and 256 per mixed arm.

## Evidence contract

For every request preserve the gateway request record and `routing_events`: mode, beta, token
counts, estimated work, eligible replicas, candidate normalized scores, selection/tie result,
reservation, prefill release, final release, queue/rejection/failover outcomes, and the final
`/_stage_a/accounting` snapshot. A valid drained arm has zero queued work, inflight requests,
and outstanding work on both replicas.

No command in this package provisions infrastructure or starts traffic.

## Recovered pilot harness launch contract

The first GPU smoke attempt is retained as diagnostic evidence at
`/workspace/e5-pilot-results/e5-pilot-20260816T040549Z-f4bda3b` (inventory SHA-256
`6f43ec2ef9ebfd95a9535843826c40072bd10f98cf7c7b2f571ba4c041dbddf8`). It sent no
requests. Its supplemental harness incorrectly treated the repository root as the E5 and gateway
package root. The packages now live at `experiments/e5` and `src/gateway`.

On a future pod, use the container's system Python (the same Python environment that provides
vLLM, Transformers, NumPy, and aiohttp), and run from the committed checkout's `experiments/e5`
directory. The explicit import roots below match this package's `pyproject.toml`; do not depend on
the login shell's working directory or an editable install.

```bash
cd '/workspace/e5-pilot-<commit>/experiments/e5'
export PYTHONPATH="$(pwd):$(cd ../.. && pwd)/src"
export HF_DATASETS_OFFLINE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
python3 -m e5.preflight \
  --tokenizer /workspace/models/Qwen--Qwen3-4B-Instruct-2507/cdbee75f17c01a7cc42f958dc650907174af0554 \
  --output-root /workspace/e5-pilot-results/preflight-<UTC>-<commit>
```

This mandatory zero-GPU command runs before either vLLM process. It imports every harness
dependency, resolves the committed gateway, checks beta/count/seeds and the tokenizer path,
tests output collision protection and metadata initialization, and runs both policies through the
real gateway against two CPU-only streaming fake backends. Any nonzero exit, wrong resolved path,
nonzero final accounting, or output collision aborts the attempt before GPU startup.

After preflight passes, preserve exact commands and PIDs under a new evidence root and launch the
two E4-equivalent services (port 8001 remains untouched):

```bash
MODEL=/workspace/models/Qwen--Qwen3-4B-Instruct-2507/cdbee75f17c01a7cc42f958dc650907174af0554
env CUDA_VISIBLE_DEVICES=0 HF_DATASETS_OFFLINE=1 HF_HUB_OFFLINE=1 NO_PROXY='*' \
  PIP_NO_INDEX=1 TRANSFORMERS_OFFLINE=1 UV_OFFLINE=1 \
  VLLM_USAGE_SOURCE=production-docker-image /usr/local/bin/vllm serve "$MODEL" \
  --served-model-name Qwen/Qwen3-4B-Instruct-2507 --tokenizer "$MODEL" \
  --tokenizer-mode auto --host 127.0.0.1 --port 8101 --max-model-len 8192 \
  --tensor-parallel-size 1 --pipeline-parallel-size 1 --seed 42 \
  --no-enable-prefix-caching
env CUDA_VISIBLE_DEVICES=1 HF_DATASETS_OFFLINE=1 HF_HUB_OFFLINE=1 NO_PROXY='*' \
  PIP_NO_INDEX=1 TRANSFORMERS_OFFLINE=1 UV_OFFLINE=1 \
  VLLM_USAGE_SOURCE=production-docker-image /usr/local/bin/vllm serve "$MODEL" \
  --served-model-name Qwen/Qwen3-4B-Instruct-2507 --tokenizer "$MODEL" \
  --tokenizer-mode auto --host 127.0.0.1 --port 8102 --max-model-len 8192 \
  --tensor-parallel-size 1 --pipeline-parallel-size 1 --seed 42 \
  --no-enable-prefix-caching
```

Only after both `/health` checks return 200, run the mandatory GPU smoke from the same E5 working
directory and environment:

```bash
python3 pilot_harness.py --stage smoke --rate 1.0 --count 4 --seed 51004 \
  --tokenizer "$MODEL" --output-root /workspace/e5-pilot-results/smoke-<UTC>-<commit> \
  --policies round_robin work_aware
```

Abort without retrying a policy arm if preflight or smoke fails, a path already exists, accounting
does not drain to zero, a service restarts, an OOM/fatal error appears, an unexpected GPU process
is present, or the paired trace hashes differ. Preserve evidence and terminate only owned process
groups. Pilot arms remain 64 requests and accept only seeds `51001` through `51004`.

## Pilot validity clarification after the diagnostic 2.5 pair

The preserved attempt at
`/workspace/e5-pilot-results/e5-pilot-20260816T045916Z-748172c` (inventory SHA-256
`3f628ddda4eed5e86e24e39607c539cd30d236199f41c5414c6d056826e7d06f`) completed
both 2.5 req/s arms but is diagnostic evidence, not the canonical completed pilot and not a basis
for measured-load selection. A retry must begin again at 2.5 req/s in a new evidence root.

Beta-weighted work is represented as binary floating point. Adding each reservation to a replica
total and later releasing its prefill/decode portions in a different order can leave harmless
roundoff near `1e-11`. The tracker now maintains an active reservation identity ledger and uses an
absolute work-zero tolerance of exactly `1e-6`. It normalizes a finite residual within that
tolerance to `0.0` only at the authoritative final-release boundary, after that replica has exact
integer-zero inflight requests and no active reservation identity. Larger residuals, nonfinite
values, active reservations, count underflow, identity mismatch, missing release, and duplicate or
inconsistent release evidence remain invalid. This boundary normalization prevents harmless
residue from affecting later work-aware scores without hiding a real accounting leak.

Gateway queueing and backend waiting are distinct signals. Gateway queue events and rejections
must remain absent/nonbinding. Backend `num_requests_waiting` is a raw performance measurement—not
an automatic infrastructure abort—and summaries retain its maximum plus the count and fraction of
positive samples. A transient maximum of one may therefore occur in an otherwise valid arm and
can help locate the load knee.

Arm summaries separately report configured offered rate, achieved request-arrival rate, arrival
lag, and completion goodput. Completion goodput must not be used to infer whether the open-loop
client maintained its arrival schedule. Existing hard aborts remain: request failure/timeout,
material gateway queueing or rejection, sustained arrival lag, OOM/restart, unexpected GPU
process, preemption, failover/health transition, prefix-cache hit, malformed routing/release
evidence, trace mismatch, or failure to drain under the accounting rule above.
