# E11 CPU preparation report

Prepared on the existing RunPod network volume `experiment-network-volume`, mounted at `/workspace`.

## Source and scope

- Source checkout: `/workspace/project` in an isolated experiment worktree.
- Base main: `98be368cb152a6479e98a9c91c77c7ac266dc73c`.
- The reviewed E10 lifecycle dependency was verified as an ancestor.
- Preparation was initially preserved uncommitted. The subsequent reviewed source commit freezes this report and implementation; execution records must capture its full SHA. Publication does not imply a merge or GPU validation.
- No GPU inference, model-weight loading, vLLM launch, infrastructure changes, or historical-evidence modification occurred.

## Implementation

The existing degraded penalty was metadata, not an effective routing action. E11 adds opt-in reduced Degraded headroom (one outstanding request / 20,000 work units) while preserving round-robin selection and existing retry/release behavior. A backend-only TTFT observer uses four comparable successful observations within ten seconds, with 350 ms degradation and 200 ms recovery thresholds and generation/epoch guards. Continued reduced traffic supplies recovery observations. Both arms use identical bounded streaming shims; only B enables lifecycle actions.

The frozen six-arm catalog, trace identity checks, fail-closed analysis, calibration/pilot gates, CPU fakes, and separately authorized future GPU commands are included. Recovery after the measured window is censored even if it occurs during drain.

## Verification

- E11: 12 tests passed (29.17 seconds), including actual fake-service harness execution, delayed recovery censoring, and mismatched six-arm evidence rejection.
- E10 regression: 37 tests passed (6.51 seconds).
- Shared gateway regression: 64 tests passed (1.40 seconds).
- Ruff lint/format and git whitespace checks passed.
- Standalone CPU preflight passed: identical 200-offer traces; delayed-replica share during injection was 50% in A versus 17% in B; B detected at 0.750 s and recovered at 0.750 s after injection ended; zero false degradations. These are scaled CPU-fake results, not GPU performance results.
- Final TCP listener inventory exactly matched the initial inventory. Port 8001 remained closed. Task-owned CPU servers were cleaned up.

Verification logs and synthetic evidence: `/workspace/project/verification/`.

## Next gate

Review and publish the source before separately authorizing GPU execution. Follow `EXECUTION_COMMANDS.md`: qualification and single-replica calibration, then paired pilot; only proceed to the frozen six measured arms after gates pass. Do not tune thresholds or repeat sweeps to force a pass. Historical E10 recovery remains censored; this preparation does not revise its results.
