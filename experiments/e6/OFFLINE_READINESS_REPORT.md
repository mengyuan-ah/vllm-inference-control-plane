# E6 offline readiness report

## Starting state and traceability

The isolated E6 branch was created from `dc1411666c690d332cb7cd9da16972165ac1b4ec`, the merge of
E5. It contains reported E5 commit `66f7d0b`. The unrelated dirty E4 checkout was not modified.

E5's measured note identifies remote evidence root
`/workspace/e5-pilot-results/e5-measured-mixed-20260913T175355Z-97d6ef0`, source commit `97d6ef0`,
pod ID `0p6zypku1ulre4`, model revision, grid, and aggregate results. It says the executed harness
included the later measured/mixed patch. The repository does not contain a recursive remote raw
evidence inventory, per-file sizes/hashes, or a digest of that exact executed patched harness.
`experiments/e5/MANIFEST.sha256` hashes an earlier checked-in harness and therefore does not close the
identity gap. This report preserves the limitation without altering E5 results. If the volume is
later reachable, capture its inventory and executed source as supplemental provenance.

## Semantics checked

- Eligibility requires a serving replica with both request-count and work headroom.
- Reservations increment inflight and outstanding work at dispatch. Prefill work is released on
  first content and remaining work on terminal completion/cancellation/failure. Release is
  idempotent and backed by an active-reservation identity ledger.
- With queue size zero, no-headroom admission immediately returns 429 `queue_request_limit`.
- C queues FIFO without reserving a replica. Dispatch occurs on release notifications. Queue
  cancellation, timeout, and loss of serving replicas remove queue accounting.
- Queue timeout is 429 `queue_timeout`; absence/loss of serving replicas is 503 and invalid for E6.
- Client streaming cancellation releases the reservation; queued cancellation removes the entry.
- E6 work ceilings are set far above homogeneous request work so request caps bind first.
- Added observational peak queue depth/work and timestamped `queue_dispatch` with residence; no
  dispatch or admission decision was changed.

## Offline validation

- Gateway suite: 48 passed, including immediate B rejection, FIFO dispatch, timeout, queued
  cancellation, streaming cancellation, idempotent release, and final empty accounting.
- E6 suite: 11 passed for trace identity, seed separation, rejection classification, SLO
  denominators, accounting validation, arrival-lag/failover/A-cap invalidation, and broken-stream
  rejection. The harness now exits nonzero on invalid arms and preserves partial evidence through
  timeout, metrics failure, cancellation, reconciliation, and gateway cleanup paths.
- Ruff: clean for gateway and E6. Python compilation: clean.
- `MANIFEST.sha256`: every listed source and protocol file verified.

## GPU readiness

The previously saved SSH endpoint `203.57.40.123:10157` refused connection. `runpodctl` is not
installed and no `RUNPOD_API_KEY` is configured, so current pod identity/access cannot be confirmed.
No pod was created, started, stopped, changed, or terminated. GPU execution must wait for an
existing compatible pod and current access details.

Required pod: exactly two RTX 4090 GPUs; qualified preserved vLLM/PyTorch/CUDA stack; one
`Qwen/Qwen3-4B-Instruct-2507` replica per GPU at revision
`cdbee75f17c01a7cc42f958dc650907174af0554`; ports 8101/8102; gateway 8080. Observe but do not
change port 8001.
