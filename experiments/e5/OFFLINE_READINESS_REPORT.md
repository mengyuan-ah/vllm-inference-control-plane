# E5 offline readiness report

Date: 2026-08-11 (America/Los_Angeles)

## Provenance and scope

- Dedicated isolated experiment checkout
- Base: E4 merge `473b4b1ac18994b0d86c0025e6dc2bab16eada4a`
- Preserved E4 inventory SHA-256: `3d7cc50fa57380e8f2e204b49e024ca298fb7c78d43679a26d8dda9e393f299d`
- Frozen estimator: `input_tokens + 107.2820167 * requested_output_tokens`
- No E5-specific files existed at the authoritative base.
- The original checkout contained unrelated untracked duplicate E3/E4 files. They were preserved
  by doing all work in an isolated Git worktree.

## Prepared offline

- Explicit `round_robin` and `work_aware` gateway modes; round-robin remains the default.
- Atomic minimum normalized outstanding-work selection and rotating deterministic ties.
- Equal default routing capacities for the two E4-equivalent replicas.
- Existing reservation lifecycle extended with reservation, first-content prefill release,
  exactly-once final release, and final accounting evidence.
- Structured decision evidence includes request identity, beta, token inputs, estimated work,
  eligibility, candidate scores, selected replica, ties, queue/rejection/failover, and releases.
- Deterministic homogeneous and exact-composition mixed trace generation with shared hashes for
  paired arms and disjoint enforced pilot/measured seed sets.
- Tentative pilot config and deliberately unresolved measured-run template.

## Verification

- Existing plus E5 gateway tests: **41 passed**.
- E5 trace/config tests: **6 passed**.
- Ruff lint: **passed**.
- Ruff formatting check: **passed after formatting**.
- Python compile check: **passed**.
- The gateway integration suite is the local two-fake-streaming-replica smoke test and covers
  completion, prestream failover, midstream failure, cancellation/client disconnect, queueing,
  heartbeat/empty SSE handling, concurrent drain, and zero reconciliation.

## Intentional stop conditions

- No RunPod was created or started.
- No GPU inference or E5 traffic was executed.
- E4 beta was not refit or modified.
- Pilot candidates are not capacity claims.
- Measured load values remain unresolved pending the separately reviewed GPU pilot.
- Nothing was committed, pushed, merged, or opened as a pull request.

Disposition: **offline-ready for a separate reviewed GPU pilot; not authorized for measured E5**.
