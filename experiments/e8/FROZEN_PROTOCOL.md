# E8 frozen protocol

## Question and interpretation

E8 estimates the paired difference `B - A` in SLO goodput. A positive difference supports the claim that this particular frozen rule improves useful throughput for this workload. It does not establish generalization to other models, shapes, hardware, thresholds, routing policies, or traffic distributions.

## Frozen system

- Two replicas, one GPU each, tensor parallel 1, pipeline parallel 1.
- `Qwen/Qwen3-4B-Instruct-2507` at revision `cdbee75f17c01a7cc42f958dc650907174af0554`.
- Exactly 1024 input tokens and 128 requested output tokens; temperature 0, ignore EOS, seed 42.
- Round robin, beta `107.2820167`, prefix caching disabled, gateway queue disabled, and backend retry limit zero.
- Request SLOs: TTFT <= 250 ms, TPOT <= 15 ms, E2E <= 2.0 s.
- Shared safety limits and timeouts are identical between configurations.
- Frozen count threshold: reject iff selected replica's pre-admission count is `>= 8`.

Configuration A evaluates and records the count rule but fails open. Configuration B enforces it. Selection, count snapshot, rule evaluation, decision record, and reservation occur under one tracker lock. Rejection advances the round-robin cursor, creates no reservation or work, performs no backend dispatch, and does not reroute. Undefined decisions and scorer errors fail open for service safety but invalidate the arm. Existing release, cancellation, and E7 shadow behavior remain unchanged outside an explicitly enabled E8 mode. A `backend_dispatch` timestamp is recorded immediately before the backend POST.

## Paired design

Measured rates are 8, 10, and 11 requests/s. Each rate has three paired replications; every arm offers 256 requests. The 18 arms therefore offer 4,608 requests. Members of a pair use identical request IDs, prompt seeds, scheduled offsets, shapes, timeouts, and safety settings. Seeds are 82001, 82002, and 82003. The catalog rotates rate order and counterbalances configuration order as far as an odd number of replications permits. Arrivals are open loop and requests are never retried.

Warmup is separate from measured arms. Between arms, wait for both backends and gateway accounting to drain to zero. Each output directory is create-once; never overwrite or append a rerun to an existing arm.

The CPU fake-stack preflight, rather than the 16-offer GPU smoke, is the mandatory deterministic
test of the threshold rejection path. It holds outstanding work so a selected replica reaches the
threshold without increasing GPU spend. GPU smoke validates serving, telemetry, traces, and clean
accounting and is not required to produce a rejection.

## Metrics and validity

The primary metric is successful requests meeting all three SLOs divided by the fixed offer window (`256 / offered_rate`). Rejections remain in the denominator. Report accepted success, prediction rejection, safety rejection, timeout, and failure counts, which must sum to 256. Latency distributions use successful generations only. Completion throughput is reported separately using its completion-observation window and is not the primary metric.

Every offer must have exactly one defined admission decision. Missing/undefined/error decisions, missing rows, unexpected failures/timeouts, nonzero final accounting, outcome mismatch, or a baseline safety-limit bind invalidate comparison. Report defined/undefined/scorer-error decision counts and reconcile backend dispatches, gateway outcomes, reservations, and final metrics.

For each rate report the three paired goodput differences (`B - A`), their mean, range, and a modest uncertainty summary (sample standard deviation and 95% t interval). Treat the interval as descriptive with only three pairs.

## Outcome claim boundaries

- Positive, valid paired differences: evidence that frozen enforcement improves SLO goodput in this exact experiment.
- Near-zero/mixed differences: no reliable benefit shown; keep enforcement deferred.
- Negative differences: frozen enforcement harms useful throughput; do not deploy it.
- Any invalid arm: make no measured comparison, preserve evidence, diagnose, and rerun only under a newly identified run directory.

Thresholds, rates, seeds, arm sizes, SLOs, routing, safety bounds, prompts, ordering, and analysis rules cannot be tuned after smoke or pilot. A required change ends this protocol and requires a new commit/protocol identity.
