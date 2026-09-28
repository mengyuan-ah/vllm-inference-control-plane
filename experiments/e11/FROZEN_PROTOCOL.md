# E11 frozen preparation protocol

Status: CPU preparation only. No GPU results and no GPU execution authorization.
Base source: reviewed E10 lifecycle implementation
`d701e45eb860b53f5c1e2838b0e4d3c6d654c56e` by ancestry. No unmerged dependency.

## Question and scoped amendment to the Phase 2 plan

Can backend-only observations detect a reachable-but-slow serving path, reduce its
traffic, and restore normal routing after the path recovers? A disables slowdown
handling; B enables it. Both use the same round-robin algorithm, requests, arrivals,
health monitoring, failure handling, instrumentation, safety caps, and shim path.

The older Phase 2 plan proposed background GPU contention and a routing-policy
comparison. This user-requested E11 instead compares handling off/on at a single routing
configuration with a bounded HTTP shim. It does **not** claim GPU compute-capacity loss,
all slow-server failure modes, or a comparison of routing algorithms.

Existing Degraded penalty is metadata only: the original score divides outstanding
work by routing capacity but never multiplies by that penalty. Merely changing it
would not implement a traffic intervention. E11 does not introduce a scoring algorithm;
it explicitly uses state-dependent headroom as its one intervention.

## Frozen workload/system

See FROZEN_SETTINGS.json and ARM_CATALOG.json (also defined in e11/protocol.py).
Qwen/Qwen3-4B-Instruct-2507, revision cdbee75f17c01a7cc42f958dc650907174af0554;
independent one-GPU replicas, TP=1/PP=1, max model length 8192, seed 42, prefix cache off.
Exact tokenizer-reencoded 1024 input / 128 output tokens, temperature zero, ignore EOS,
streamed usage and length finish. Work beta 107.2820167; normal caps 8 inflight and
100,000 work per replica. Queue and prediction admission disabled. One retry maximum,
only before content; no replay of after-content interruptions. E10 corrected receipt
helpers and backend-failure paths are reused unchanged in semantics. E10 constants
match this exact workload: TTFT <=250ms, TPOT <=15ms/token, E2E <=2s, output count 128.
Those helpers are not generic classifiers for arbitrary E11 workloads.

## Injection, isolated from the detector

Two identical task-owned CPU shims proxy requests to the two backends. Only the
selected target delays **dispatch to the upstream backend** by 750ms once per POST
whose shim arrival is in [15s,45s). No host/GPU settings or unrelated services change.
Headers used for transport may change; request body and response stream bytes, usage,
finish markers, status, and ordering are preserved. Health checks bypass the delay and
proxy real backend health. SSE transfer is unbuffered after dispatch; automatic HTTP
decompression is disabled. Delay is additive to dispatch-to-first-content seen by the
gateway, not gateway admission/queue time. Requests admitted just before 45s can finish
their already-selected delay after the interval ends; record their actual delay.

Record scheduled/actual start and end, target, each shim arrival, selected delay, and
actual sleep duration. Maximum configurable shim delay is 2s; protocol uses 750ms only.
The detector gets no target, injection times, or injection flag. Injection events are
joined with detector events only offline.

## Detector and traffic action

Backend attempt dispatch time is captured after admission/reservation, immediately
before POST. First-content time is captured before downstream forwarding. On a complete,
valid backend response, record backend TTFT = first-content minus attempt dispatch.
Thus preprocessing, gateway queue residence, and client-side receipt waits are excluded.
Completion is required so failed/error streams do not earn recovery evidence. This
means detection latency includes waiting for successful completion, not just first byte.

Only comparable 1024/128 requests in Healthy or Degraded are eligible. Use the latest
four eligible observations (completion-observation order), with dispatch age <=10s.
Four consecutive values strictly >350ms cause Healthy -> Degraded. Four strictly <200ms
cause Degraded -> Healthy. Values in the hysteresis band break the corresponding
four-sample condition. Buffers clear on lifecycle/generation identity change. No sample
expiry or successful /health check restores a degraded replica. With no fresh traffic,
state remains degraded and recovery is censored.

Degraded stays serving-eligible with **one inflight request and 20,000 work**. A realistic
request costs 14,756.0981376 and fits. The normal caps remain identical in A/B; only B
can enter Degraded through the latency detector. This concurrency reduction shifts
traffic when the slow path is occupied, while retaining requests as recovery evidence.
At low enough load it need not change the traffic share; the pilot must demonstrate
actual reduction at the frozen operating point. No automatic rate/threshold tuning.

Every attempt captures generation and lifecycle epoch. Old attempts cannot contribute
across a state transition or generation change, and the final state mutation atomically
compares expected lifecycle/generation/epoch under the tracker lock. Unhealthy/HalfOpen/
Recovering are owned by existing E10 health/failure logic, not the latency detector.
Genuine passive and active failures continue to exclude, probe, and recover as in the baseline lifecycle implementation.
A/B record identical observations; A records proposed decisions without applying them.

## Bounded stages and seeds

No automatic sweeps or extra runs:

1. CPU tests and CPU-only fake pair (separate scaled timings, seed119001; never GPU data).
2. After separate authorization, runtime/ownership qualification and one calibration:
   one backend at 3 req/s for 20s / 60 offers, seed110000, no injection. Both logical
   shim entries point to the same prequalified backend URL for this stage only.
   Require >=99% SLO success, >=99% TTFT <200ms, no failures/rejections, valid metrics.
3. One paired pilot A then B with the full 90s timeline, seed110001, target1. Require
   valid evidence, no failed/rejected requests, exact paired trace, one degrade/restore
   cycle in B, no false degradation, recovery within window, and lower target slow-phase
   dispatch fraction in B than A. Failure stops; do not tune using pilot/measured data.
4. Six measured arms: r1-A, r1-B, r2-B, r2-A, r3-A, r3-B. Seeds111001/111002/111003;
   targets replica1/replica2/replica1. Each is 270 offers at3req/s across90s.
   Phases: pre [0,15), injected [15,45), recovery [45,90). Drain at most10s with
   6s client timeout and 5s backend timeout. Each arm creates fresh gateway/shims;
   backend state must be drained and healthy, with unchanged generations.

Calibration/pilot/measured total: 2,220 offers, 740 scheduled seconds, excluding setup,
drain and external runtime qualification. All stages require clean reviewed source.
Canonical prompts, IDs, token IDs/hashes and every scheduled offset match within pairs.
The E10 exact-prompt helper retains its historical text prefix; E11 seeds distinguish
these traces and no E10 historical evidence is reused as observations.

## Analysis and validity

Report per-arm SLO goodput (SLO-success count /90s), all-offer success fraction, before/
slow/after target dispatch share, detection delay, restoration delay or censoring,
false degradation, transition count, raw backend observations, client timings/outcomes,
actual applied delay, queue/reservation accounting, and paired B-minus-A differences.
No absence of detection/recovery is silently dropped: it is an outcome, not by itself
an invalid measured arm. Report all three paired repetitions; do not make a significance
claim from a pooled average alone.

Require six exact catalog arms and order (no overlap), source/settings identity,
270 unique rows each, full content and schedule hashes, consistent client outcomes,
complete backend observation coverage, no post-content retry, <=1 pre-content retry,
zero final work/inflight/reservations, responsive health, zero sampled backend waiting,
no preemption/cache-hit counter changes, no unexpected failure lifecycle or restart.
Sample backend health/metrics every250ms. Sampling gaps >1s, missing metrics, arrival
lag >100ms, or injection marker/sleep deviation >100ms invalidate; do not hide them.
Sampling does not prove absence of an event between samples; before/after counters
and runtime logs provide complementary evidence. Preserve failures under unique roots.

## Evidence and historical boundary

Each arm writes manifest/config/source, canonical client rows, detector observations,
lifecycle transitions, injection records, and backend samples. Preserve runtime identity,
expanded launch argv/logs, CPU qualification, pilot gate, catalog/settings, and source SHA
beside the campaign. Never overwrite earlier roots. Inventory excludes itself before
archiving. No archive/evidence download to the local machine is needed.

E10 demonstrated failed-replica exclusion and survivor continuity. Full real-GPU return
to Healthy was not observed within its60s window and remains censored. CPU recovery
checks and future E11 slow-path recovery must not retroactively change that conclusion.
