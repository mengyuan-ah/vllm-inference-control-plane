# E10 failure and staged recovery

The bounded GPU campaign has completed; see [the execution report](EXECUTION_REPORT.md)
for results, recovery censoring, and the verified evidence archive location.
The experiment design is described in `FROZEN_PROTOCOL.md`.
The focused gateway feature is disabled unless
`GATEWAY_LIFECYCLE_ENABLED=1`; existing E8/E9 behavior therefore remains unchanged.

`cpu_preflight.py` starts two experiment-owned fake HTTP/SSE replica process groups, drives
open-loop requests, verifies and SIGKILLs only registered replica1, observes survivor service,
restarts the identical fake command as a new generation, observes active health plus a separate
inference probe and staged rejoin, and checks final accounting. It stops only its own processes.

Use `EXECUTION_COMMANDS.md` for clean-checkout verification and the future reviewed GPU workflow.
The measured offered rate remains deliberately unfrozen until the bounded one-replica pilot passes.

## Outcome/recovery follow-up

See [OUTCOME_FIX_REPORT.md](OUTCOME_FIX_REPORT.md) for CPU-only outcome-labeling
corrections, recovery regression coverage, and E11 reuse guidance. Historical evidence
and its censored recovery conclusion are unchanged.
