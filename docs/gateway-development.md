# Phase 2 Stage A gateway

Lightweight local implementation of the frozen Stage A milestone. The final public tree retains
the canonical implementation, experiment protocols, and compact reviewed results.

Implemented scope:

- `POST /v1/completions` streaming proxy matching the Phase 1 request shape
- Frozen request lifecycle and terminal-state enforcement
- Healthy round-robin with atomic selection and reservation
- Healthy, degraded, unhealthy, half-open, and recovering replica eligibility
- Per-replica in-flight and estimated-work headroom
- Count/work-bounded FIFO queue and static queue timeout
- Deterministic HTTP 429 versus 503 decisions
- Exactly-once reservation release with an active identity ledger and finite drained-work
  normalization at an absolute `1e-6` tolerance
- One alternate-replica retry before first non-empty content
- Raw SSE forwarding, final usage capture, and monotonic gateway timing boundaries
- Environment-configured one- or two-replica startup
- Read-only `/_stage_a/accounting` reconciliation endpoint
- Deterministic fake streaming backends for local integration tests

The whitespace token counter and `alpha=beta=1` defaults are nonbinding development settings.
The GPU experiments used the exact Qwen tokenizer, and E4 subsequently calibrated the scalar work
model for the tested workload. Those measured settings remain experiment-specific rather than
production defaults.

## Local checks

From the repository root:

```bash
make check
```

The integration tests bind ephemeral localhost ports for the gateway and two
fake backends. They do not require internet, GPUs, vLLM, PyTorch, or model
weights.

## Development server

With real backends configured at localhost ports 8101 and 8102:

```bash
GATEWAY_TOKENIZER_MODEL=Qwen/Qwen3-4B-Instruct-2507 \
.venv/bin/python -m gateway
```

The gateway listens on `127.0.0.1:8080`. This entry point originated as the RunPod integration
gate and was later exercised by the controlled GPU experiments. That evidence validates the tested
lab configurations, not production readiness.
Set `GATEWAY_REPLICA2_ENABLED=0` for the replica-1-only integration gate. Exact
tokenizer loading uses only files already present in the shared Hugging Face
cache (`local_files_only=True`).

### Runtime configuration

The launcher exposes the canonical routing, slowdown, and recovery limits without adding a
separate configuration framework. Boolean values accept `1/0`, `true/false`, `yes/no`, or
`on/off`.

| Setting | Environment variable |
| --- | --- |
| Slow-replica handling | `GATEWAY_SLOWDOWN_ENABLED` |
| Routing policy | `GATEWAY_ROUTING_MODE` |
| Per-replica normal headroom | `GATEWAY_REPLICA{1,2}_MAX_INFLIGHT`, `GATEWAY_REPLICA{1,2}_MAX_WORK` |
| Per-replica degraded headroom | `GATEWAY_REPLICA{1,2}_DEGRADED_MAX_INFLIGHT`, `GATEWAY_REPLICA{1,2}_DEGRADED_MAX_WORK` |
| Per-replica recovery headroom | `GATEWAY_REPLICA{1,2}_RECOVERY_MAX_INFLIGHT`, `GATEWAY_REPLICA{1,2}_RECOVERY_MAX_WORK` |
| Work-aware routing capacity | `GATEWAY_REPLICA{1,2}_ROUTING_CAPACITY` |

When slowdown handling is enabled, both degraded-headroom values must be explicitly configured
for every enabled replica. Recovery defaults are one request and `20,000` estimated work units
for each replica.
