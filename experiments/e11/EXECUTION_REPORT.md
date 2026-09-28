# E11 execution report

Executed source: `99438da2ee35c4ce466b0293a804f0ca3e7a8127` from the reviewed experiment checkout.

## Status

PASS: fixed calibration, both pilot arms, all six measured arms, and final campaign analysis. No retuning, sweeps, or replacement measured arms. Raw evidence remains unchanged on the network volume.

## Measured results

| Pair | A SLO goodput (req/s) | B SLO goodput (req/s) | Difference | Target share A / B during delay | B detection / recovery (s) |
|---|---:|---:|---:|---|---|
| r1 | 2.5000 | 2.8111 | +0.3111 | 50.0% / 18.9% | 4.601 / 5.304 |
| r2 | 2.5000 | 2.8000 | +0.3000 | 50.0% / 20.0% | 4.273 / 7.310 |
| r3 | 2.5000 | 2.8111 | +0.3111 | 50.0% / 18.9% | 4.608 / 5.312 |

Across 810 offers per condition, A had 675 SLO successes and B had 758: 83 additional SLO successes. All 1,620 measured offers completed successfully without retries; SLO misses are not transport failures. B detected and recovered in all three repetitions with no false degradations. No recovery was censored.

## Validity and provenance

- Exactly six catalog arms in the frozen order, 270 unique offers each, and matching canonical traces within every pair.
- One clean source commit, fixed settings, real tokenizer-verified 1,024-token prompts and 128-token outputs.
- Qwen/Qwen3-4B-Instruct-2507 revision `cdbee75f17c01a7cc42f958dc650907174af0554`; vLLM 0.25.1+cu129; independent RTX 4090 GPUs 0/1; prefix cache disabled.
- Runtime and model metadata, expanded process argv/GPU bindings, per-request rows, lifecycle/detector/injection events, and sampled backend metrics are preserved.
- Analyzer passed arrival timing, metrics/counter, injection identity, observation coverage, dispatch trace, and final accounting checks.
- The 60-offer calibration passed 100% SLO success; paired pilot passed all frozen gates. Calibration/pilot evidence is separate from measured evidence.

## Interpretation and limits

For this workload and the bounded 750 ms serving-path dispatch delay, observation-driven headroom reduction shifted traffic away from the slow path and increased SLO goodput in each of three pairs. Normal routing recovered after the delay ended. This is not a test of GPU contention, arbitrary failures, general workload robustness, or statistical significance. The historical E10 recovery conclusion remains unchanged.

## Cleanup and storage

- Only the two E11-owned backend process groups were terminated; CPU gateways/shims were cleaned up by their harnesses.
- Final TCP listener identities exactly match the pre-execution inventory. Port 8001 remained untouched and closed; ports 8101/8102 are closed.
- GPU process inventory is saved in `runtime-after.json`. The pod itself was not stopped or terminated.
- Evidence directory: `/workspace/e11-results/e11-20260920T222928Z-99438da`.
- `SHA256SUMS` inventories all finalized files except itself. The sibling tar.gz and its SHA-256 sidecar are retained on the network volume; no raw evidence or dependencies were downloaded locally.

## Evaluation

SLO success rose from 675/810 (83.33%) to 758/810 (93.58%): +10.25 percentage points. Mean SLO goodput improved by 12.3%, while SLO misses fell by 61.5%. Each of the three paired repetitions improved. These are descriptive results, not a statistical-significance claim.

The intervention avoided delayed requests; it did not accelerate them. Remaining delayed requests still violated TTFT and end-to-end SLOs, with enabled-arm p99 TTFT around 840–845 ms. There were no TPOT violations, so E11 does not validate detection of slow token generation. After Healthy restoration, target dispatch shares returned to 60/119, 57/113, and 60/119 (approximately 50%). Keep the feature opt-in; these results do not establish production readiness.

## Archive integrity

- Archive: `/workspace/e11-results/e11-20260920T222928Z-99438da.tar.gz`
- Size: 48,317,473 bytes.
- SHA-256: `de534527f6c6bb4307ed62064b1684d3740e6acc82fbe98206e21dcb463a3b98`
- All 92 inventoried files verified directly inside the archive; safe paths, unique members, and no unexpected files. The inventory itself is excluded from its own file list.
- This publication is derived from the preserved report and request-row evaluation; the historical archive was not modified.

## Independent closeout audit

The [Phase2 closeout audit](../../artifacts/summaries/e11-audit-report.md) independently verifies archived request/event rows, reproduces the reported results, and adds arrival-cohort, client-recovery and drain-denominator comparisons. It documents historical size/stream/source-attestation limits and shutdown-only log errors. [Machine-readable results](../../artifacts/summaries/e11-audit-summary.json) are included; complete raw evidence is maintained separately.
