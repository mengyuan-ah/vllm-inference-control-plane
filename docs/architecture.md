# Architecture and request lifecycle

## Design goal

The control plane is deliberately smaller than vLLM. It does not schedule GPU kernels or split a
model across devices. Each backend is an independent vLLM server holding a full model copy. The
gateway owns request admission, replica choice, failure semantics, and client-visible streaming.

## Components

### Gateway

The canonical implementation is under [`src/gateway`](../src/gateway). It uses `aiohttp`
for the client-facing server and backend streams. Core modules separate configuration, admission,
replica state, reservation tracking, token-work estimates, routing, lifecycle transitions, and
slowdown handling.

### Replicas

The measured topology used two independently addressable vLLM processes, one per RTX 4090. Each
replica exposed the same OpenAI-compatible completion endpoint and model revision.

### Experiment harnesses

E5-E11 add bounded orchestration around the gateway rather than replacing the inference engine:

- E5: round-robin versus outstanding-work-aware routing
- E6: headroom limits and queueing
- E7: shadow prediction without enforcement
- E8: enforced admission under sustained overload
- E9: smooth versus burst-concentrated arrivals
- E10: crash, exclusion, restart, and content-aware failover
- E11: reachable-but-slow replica detection, traffic reduction, and recovery

## Request lifecycle

1. Validate the request and estimate token work.
2. Select an eligible replica with sufficient count and work headroom.
3. Atomically reserve the request in the accounting ledger.
4. Dispatch to the backend and forward raw SSE frames.
5. Record the first non-empty content boundary and final usage.
6. Release the reservation exactly once at a terminal outcome.

When no replica has headroom, the configured experiment either queues within a fixed bound or
rejects with a deterministic status. Queue residence and gateway preprocessing time are measured
separately from backend service time.

## Failure boundary

The gateway treats content receipt as the replay-safety boundary:

- Before non-empty content: one alternate-replica attempt may be made.
- After content: the stream is not replayed because doing so could duplicate client-visible output.

E10 exercised this distinction during replica termination. The fault campaign preserved survivor
continuity, but the observation window ended before the restarted replica returned to fully Healthy.

## Slow-replica policy

E11 measures backend TTFT from dispatch to first content, excluding gateway queue time. Four fresh,
comparable observations above the frozen high threshold transition a replica to Degraded. In that
state, reduced count/work headroom shifts most traffic to the healthy replica while retaining enough
observations to detect recovery. Four observations below the low threshold restore Healthy.

This mechanism changes routing opportunity; it does not accelerate requests already executing on a
slow backend.

## Observability

The project records client timings, per-replica decisions, lifecycle transitions, request outcomes,
SLO classifications, accounting snapshots, and sampled vLLM/GPU metrics. Reports distinguish direct
measurements, sampled values, derived values, and interpretations.

## Non-goals

- Reimplementing vLLM scheduling or PagedAttention
- Claiming production readiness or universal capacity
- Retrying streams after client-visible content
- Inferring kernel or HBM bottlenecks without profiler evidence
- Treating sampled GPU utilization as proof of serving efficiency
