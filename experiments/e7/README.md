# E7 request-level shadow prediction

E7 records pre-dispatch predictions of whether a successful request will violate TTFT 250 ms,
TPOT 15 ms/token, or E2E 2 seconds. Predictions never affect admission, routing, or dispatch.

See `PROTOCOL.md` for the frozen experiment, `run_harness.py` for execution, and
`FOLLOWUP_REVIEW_FIXES_20260913.md` for the post-review safety fix and preserved-row coverage
audit. `audit_preserved.py` reproduces the read-only archive audit into a new output path.
