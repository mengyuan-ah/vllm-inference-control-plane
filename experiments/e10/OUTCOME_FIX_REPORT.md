# E10 outcome and recovery follow-up (CPU only)

Base: current GitHub main `a9e4fc1a421d32464d0eefc8db9d0d7a246cf1ac`, fetched on
2026-09-20. Work performed entirely on RunPod volume `experiment-network-volume` at `/workspace`.
No historical request rows, inventories, archive, or execution report were rewritten.

## Root cause and correction

The harness observed client first-content timing but its final outcome fallback
ignored that evidence. Every non-timeout/non-rejection failure became
`failed_before_content`, including the six historical interrupted streams.

New rows explicitly record `content_received`, set only on parsed nonempty generated
text, alongside TTFT. Exceptions/cancellation preserve this observation in `finally`.
`classify_outcome` selects before/after-content failure from client receipt, not HTTP
200, error messages, gateway state, or backend completion. Timeouts/cancellation retain
separate diagnostic flags; after-content failures use `failed_or_truncated_after_content`.
Success still requires content, DONE, length finish, exactly 128 usage output tokens,
and no stream/transport error. Missing or contradictory evidence produces
`invalid_evidence` plus diagnostic errors, not a guessed ordinary failure. Version-2
manifests require these observations during analysis. Legacy evidence remains legacy;
this change does not silently relabel the archived campaign.

Admission rejection and successful pre-content failover remain distinct. A complete
backend/gateway result cannot override incomplete client receipt or count it as SLO
success. Numeric usage coercion no longer permits fractional/string token counts.

## Shared recovery defect found by regression tests

The existing 20,000-work reduced cap already admits a representative request:
`1024 + 107.2820167 * 128 = 14756.0981376`. No cap or promotion threshold was changed.

Two newly exercised recovery cases failed before the shared fix: a midstream backend
disconnect did not record passive failure, leaving Recovering eligible; a stream error
followed by completion markers could earn a recovery success. Gateway now rejects
explicit stream errors as completion and records after-content backend failures for
passive exclusion. It does not retry these streams. Downstream client disconnects
remain cancellation, not backend failure. Reservation release remains idempotent;
all tested terminal paths reconcile to zero inflight, work, and reservations.

CPU tests exercise Unhealthy/HalfOpen exclusion, a single controlled readiness probe,
reduced concurrency with realistic work, four successful gateway requests promoting
to Healthy, restored normal concurrency, failure re-exclusion, and manager cleanup.
The existing active-health test covers automatic health-to-probe transitions.

## Verification and E11 reuse

E10 tests and gateway tests cover client disconnect/timeout/error, valid completion,
one pre-content failover, admission rejection, cancellation, incomplete receipt despite
gateway completion, invalid usage, and missing/contradictory evidence. CPU preflight
now uses realistic 1024/128 work and four recovery successes instead of tiny requests.
The process-identity test uses the existing wait-for-exec registration helper to avoid
observing an empty command during subprocess startup; production injector is unchanged.

E11 is not present at this base. Reuse `e10.runtime.StreamObservation`,
`classify_outcome`, `run_harness.offer`, and the shared gateway lifecycle/tracker;
do not duplicate the old outcome fallback. Keep client-receipt success separate from
gateway completion, use the v2 evidence fields, and retain representative-work recovery
tests and the <=1 pre-content / no post-content retry boundary.

This CPU verification does not establish real GPU recovery latency or change the E10
result. Full return to Healthy was **not observed in the historical 60-second window**;
that result remains censored. No GPU workloads, model loads, vLLM restarts, or measured
campaigns were launched for this fix. Existing evidence and unrelated work were untouched.

Validation logs and fake preflight outputs are under `/workspace/project/tools/`.

Working-tree verification: **37 E10 tests passed; 64 gateway tests passed**. Ruff
lint, formatting (49 files), byte compilation, and realistic-work CPU preflight passed.
The two new recovery regression cases were observed failing before the shared fix.
