# E10 offline preparation report

Base commit: `cbca91e283681b8d4f517495f42cf0d4c19f9d8c`.

Existing gateway support included lifecycle names, normal exclusion of Unhealthy/HalfOpen,
Recovering capacity limits, a single pre-content retry, no retry after content, and idempotent
reservation release. It did not actively drive lifecycle state, distinguish health from inference
readiness, serialize half-open probes, record transitions, stage recovery, or validate complete
SSE termination/usage.

This branch wires an opt-in active/passive lifecycle manager, transition evidence, readiness
probes, bounded recovery, complete-stream validation, generation-aware metrics, safe owned-PGID
fault injection, paired-trace/reconciliation analysis, and CPU preflight. E8 prediction enforcement
is disabled by protocol, the queue is disabled, and the sole retry is restricted to pre-content
failure. Prometheus is observational only.

No GPU work, vLLM process, model load, or inference traffic was started during preparation.
