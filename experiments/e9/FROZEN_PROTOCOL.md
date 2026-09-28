# E9 frozen protocol

## Question

Does the frozen E8 admission rule protect SLO goodput and limit backlog when
arrivals use the specified burst shape, despite the same average rate as smooth
traffic? Improvement, no effect, and degradation are all legitimate outcomes.

## Frozen system

- Two independent replicas, one GPU each, TP=1 and PP=1.
- `Qwen/Qwen3-4B-Instruct-2507` at revision
  `cdbee75f17c01a7cc42f958dc650907174af0554`.
- Exactly 1,024 input tokens and 128 generated tokens; temperature 0, ignore EOS,
  seed 42.
- Round robin, beta `107.2820167`, prefix caching disabled, gateway queue
  disabled, and backend retry limit zero.
- TTFT <= 0.250 s, TPOT <= 0.015 s/token, and E2E <= 2.0 s.
- Identical permissive safety limits and timeouts in all conditions.
- Configuration A observes the count rule and admits. Configuration B rejects
  iff the selected replica's pre-admission count is at least 8.
- Selection, observation, decision, and reservation remain atomic. Rejection
  consumes the round-robin turn, creates no reservation, dispatch, or reroute.

For this workload the rule is a selected-replica count cap, not a new predictor.

## Four conditions and exact traces

Each repetition crosses smooth/bursty traffic with A/B admission.

Smooth has offsets `i / 8` for `i=0..255`. Bursty has eight four-second
cycles and offsets `4*c + j/16` for `c=0..7`, `j=0..31`. Each burst occupies
`[4*c, 4*c+2)`; `[4*c+2, 4*c+4)` is quiet. Both patterns offer 256 requests
over the same fixed 32-second window. The quiet tail remains in the denominator.

Measured prompt seeds are 92001, 92002, and 92003. Within a repetition all four
conditions share request IDs, prompts, ordering, and token shapes. A/B members
share the complete scheduled trace. Smooth/bursty differ only in offsets and
cycle metadata. Content and scheduled-trace hashes are preserved separately.
The exact counterbalanced order is normative in `ARM_CATALOG.json`.

The schedules are deterministic, not independent arrival-pattern samples.
Repetitions vary prompts and execution conditions; conclusions apply only to
this burst shape.

## Timing and evidence

Prompt generation and baseline telemetry finish before the monotonic offer clock
starts. Offers are open loop, independently scheduled, and never retried.
Evidence includes scheduled/actual send times, arrival lag, UTC arm boundaries,
monotonic duration, actual arm index, exact prompts and input token IDs, usage,
finish reason, stream completion/error indicators, first-content/completion
timing, admission decisions, dispatch events, and 50 ms state samples.

The harness observes the whole 32-second window, including the final quiet
interval, then drains. Output directories are create-once and partial evidence
is retained on failure. Warmup is separate and every arm begins only after
gateway and both backends drain.

Stream chunks, not individual token timestamps, are observed. No individual-token
timing claim is permitted.

## Backlog and recovery

Gateway queue depth/work, gateway inflight/outstanding work, and vLLM
running/waiting are separate metrics. For each high-rate boundary at `4*c+2`,
report outstanding requests/work, sampled time to zero, whether zero occurs
before the next burst, and residual state at the next burst. A non-draining
interval is censored. The maximum observed sampling interval is reported.

Quiet intervals contain no requests and cannot demonstrate restored request
latency. Report all-offer and first-eight-offer cohorts per cycle, retaining
rejections in denominators. These are observations at subsequent arrivals, not
continuous time-to-SLO-recovery probes. Cycle zero is interpreted separately.

## Analysis

Primary goodput is SLO successes divided by 32 seconds. Fast 429 responses never
enter generation-latency percentiles. Report the following paired contrasts per
seed and modest descriptive summaries across three repetitions:

1. B-A under smooth arrivals.
2. B-A under bursty arrivals.
3. Bursty-smooth under A.
4. Bursty-smooth under B.
5. Difference in admission effects: (bursty B-A) - (smooth B-A).

Also report outcome counts, SLO fraction, accepted latency, per-cycle cohorts,
backlog peaks/recovery, achieved timing, decision coverage, scorer errors,
backend metrics, and final accounting. Missing metrics are not zeros.
Backend waiting is an outcome, not an automatic retrospective invalidity rule.
