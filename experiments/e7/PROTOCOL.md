# E7 frozen offline protocol

## Scope and deliberate narrowing

E7 asks whether information available after round-robin selects a replica, but before backend
dispatch, identifies requests that will miss at least one client-observed target: TTFT <= 250 ms,
TPOT <= 15 ms/token, and E2E <= 2 seconds. It is shadow-only. No prediction may reject, delay,
reroute, retry, or change release accounting.

The older Phase 2 plan proposed predicting queue wait plus service time and testing mixed and
bursty traffic. This run deliberately narrows to the requested homogeneous 1,024-input/128-output
shape, no gateway queue, and request-level classification. Mixed/bursty prediction is deferred.
E6 request rows lack atomic pre-dispatch features, so they are motivation only, never calibration.

## Fixed topology and workload

- Two equivalent RTX 4090 replicas, one full model copy per GPU, ports 8101/8102.
- Qwen/Qwen3-4B-Instruct-2507 revision
  `cdbee75f17c01a7cc42f958dc650907174af0554` under vLLM 0.25.1+cu129.
- Round-robin; request cap 128 per replica; max work 1e9; queue disabled.
- E4 beta remains `107.2820167` for work accounting. It is not tuned.
- Exact 1,024 prompt and 128 completion tokens; streaming, temperature zero, ignore EOS.

## Prediction point, features, and outcomes

Under the tracker lock, immediately after choosing a replica and before incrementing its ledger,
capture monotonic timestamp, selected replica, selected-replica in-flight request count, and
selected-replica outstanding work. After reservation returns and before response preparation or
backend dispatch, compute all shadow decisions and append one event. No synchronous metrics call
is made. Features older than one second or incomplete yield an undefined threshold decision.

Three predictors score each measured request:

1. Always pass.
2. Predict miss when selected-replica in-flight-before >= a frozen threshold.
3. Predict miss when selected-replica outstanding-work-before >= a frozen threshold.

The work rule is the sole additional predictor. With fixed request shape it may add nothing beyond
count; it is retained because prefill release changes outstanding work during a request. Thresholds
maximize balanced accuracy over complete valid calibration arms, tie-breaking by fewer false
positives and then lower threshold. Measured traces are wholly separate and cannot revise them.

Only clean HTTP 200 streams with first content, `[DONE]`, no SSE error, finish reason `length`, and
exact 1,024/128 usage receive latency labels. For these, equality passes and strictly greater than
any threshold is a miss. All other outcomes are unexpected failures with an undefined SLO label;
they are reported separately and invalidate the arm.

## Bounded calibration catalog

Candidate rates are 6, 8, 10, 11, and 12 req/s, motivated by E6's all-pass 6 req/s result and an
estimated two-replica service capacity near 9 req/s. Maximum rate is 12 req/s, maximum arms five,
96 requests per arm, arm timeout 180 seconds, and overall deadline 900 seconds. Seeds
71001..71005 are calibration-only and map in rate order. Stop escalation on OOM, restart, broken
stream, preemption, unexpected failover, sustained send lag (p95 >50 ms or max >250 ms), or a
binding safety cap. Backend waiting and latency misses are expected. The useful target is one
mostly-pass, one mixed, and one frequent-miss valid arm. If every safe arm passes, stop as
inconclusive and do not run an all-pass measured evaluation.

Before calibration, run an eight-request smoke at 6 req/s with seed 70001. Verify real streaming,
prediction/event pairing, final release, empty accounting, and evidence files.

## Frozen measured evaluation

After calibration, commit or immutably record `predictor-parameters.json`, the selected low/mixed/
high rates, seeds, source identity, commands, and definitions. Run three selected rates by seeds
72001/72002/72003: nine arms, 256 requests each, 2,304 offers. Rotate rate order low/mixed/high,
mixed/high/low, high/low/mixed across repetitions. Warm-ups are separate and excluded. Drain fully
between arms.

Each arm reports offered/achieved rate and lag; successes and SLO prevalence; TTFT/TPOT/E2E
p50/p95; per-metric miss counts; TP/FP/FN/TN, precision, recall and false-positive rate for every
predictor; undefined zero-denominator metrics as null; prediction computation p50/p95; backend
waiting; dispatch balance; metrics deltas; and final accounting. Cross-run aggregates pool request
rows; per-run variation is retained.

## Preservation and safety

Before traffic, verify the E6 archive at
`/workspace/e6-results/e6-rerun-aa48f25-20260913.tar.gz` has SHA-256
`fb2ae5b7b89465d6b0fad859e627ab0e9421fe884ca39be43ac2591264c8952f` and do not modify it.
Capture port 8001 initial/final state and preserve it. Every create-once arm directory retains its
trace before requests begin and writes partial records/events/accounting even after timeout. Keep
source, parameters, commands, runtime identity, backend metrics, logs, summaries, and a recursive
size/SHA-256 inventory. Stop only owned gateways/harnesses; leave pre-existing replicas and the pod
unchanged. Shadow results cannot establish that E8 rejection improves SLO goodput.
