# E11: slow serving-path detection, reduction, recovery

**Bounded execution complete; independently audited with limits.** See [execution report](EXECUTION_REPORT.md) and [CPU evidence audit](../../artifacts/summaries/e11-audit-report.md).

[FROZEN_PROTOCOL.md](FROZEN_PROTOCOL.md), [PREPARATION_REPORT.md](PREPARATION_REPORT.md) and [EXECUTION_COMMANDS.md](EXECUTION_COMMANDS.md) preserve the historical pre-execution freeze; they are not a claim that execution is still pending.

- Shared gateway detector: backend-only TTFT, four comparable observations, hysteresis,
  generation/epoch guards; disabled by default.
- Existing round-robin remains unchanged. Degraded headroom1 / work20000 actually reduces
  traffic under concurrency; old penalty metadata is not falsely presented as routing.
- Symmetric CPU delay shims preserve request/response bytes and bypass delay for health.
- Corrected E10 client observation/outcome helpers retain the1024/128 workload/SLO constants.
- A/B catalog: three paired repetitions, target1/2/1, identical within-pair requests/arrivals.
- Future calibration/pilot gates prohibit silent sweeps and measured execution without gates.
- CPU fake services bind ephemeral loopback ports; port8001 and unrelated services are untouched.

E10's full GPU recovery remains unobserved/censored in its historical60s window. This
preparation and its fake-service checks do not turn that into a GPU recovery result.

## Completed bounded execution

See [EXECUTION_REPORT.md](EXECUTION_REPORT.md) for the six-arm GPU results, limitations, executed source identity, and network-volume archive checksum. Preparation documents describe the pre-execution freeze; the execution report records the subsequent authorized run.
