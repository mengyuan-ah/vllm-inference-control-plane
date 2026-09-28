# E11 independent evidence audit

Verdict: **verified with explicit evidence limits**. Independent CPU recomputation reproduces the reported six measured arms, pooled A 675/810 and B 758/810 SLO successes, three detection/restoration cycles and no false degradations. No numerical discrepancy with archived analysis was found. Validity and treatment benefit are separate findings; three pairs do not establish statistical significance or general robustness.

## Source and frozen design

Executed source: `99438da2ee35c4ce466b0293a804f0ca3e7a8127`. The E11 implementation and report were already present in the reviewed source tree at closeout. Archived protocol/settings/catalog match that executed Git commit byte-for-byte. The recorded source is clean; this is not an independent attestation of every executed filesystem byte.

Qwen/Qwen3-4B-Instruct-2507, revision `cdbee75f17c01a7cc42f958dc650907174af0554`; two independent RTX 4090 replicas, TP=1/PP=1, max length8192, seed42, prefix cache disabled. Recorded vLLM `0.25.1+cu129`, Torch `2.11.0+cu129`, Transformers `5.13.1`, aiohttp `3.14.1`. Expanded argv, model endpoints, per-process GPU bindings and logs corroborate identity/settings; model weights were not loaded or rehashed during this audit.

Exact 1024-input/128-output requests at3req/s for90s, 270 offers/arm. TTFT≤250ms, TPOT≤15ms/token, E2E≤2s, complete length-finished streams. Same round-robin, traces and safety caps in A/B; only B enables slowdown handling. Normal headroom8/100000 work; Degraded1/20000. Queue/prediction admission disabled. Four comparable completed backend observations trigger degradation (>350ms) or restoration (<200ms), with freshness/generation/epoch checks. See the unchanged [frozen protocol](../../experiments/e11/FROZEN_PROTOCOL.md) and [catalog](../../experiments/e11/ARM_CATALOG.json).

Both arms use symmetric CPU HTTP shims. The target inserts750ms **before upstream dispatch** for shim arrivals in[15,45)s; health bypasses delay. This is serving-path delay, **not reduced GPU compute capacity**. Targets are replica1/replica2/replica1 by repetition; order r1-A,r1-B,r2-B,r2-A,r3-A,r3-B is verified from nonoverlapping start/end timestamps.

## Independent results

Whole-arm goodput uses the frozen90s offer window. Pooled goodput sums successes over270s per treatment: A2.500000, B2.807407req/s. All1,620 requests completed with no failures, rejections or retries. A has135 SLO misses; B52.

| Arm | SLO / offered | Completed | Failed / rejected | Whole goodput (/s) | Drain-inclusive (/s) |
| --- | --- | --- | --- | --- | --- |
| r1-A | 225/270 | 270 | 0 / 0 | 2.500000 | 2.471448 |
| r1-B | 253/270 | 270 | 0 / 0 | 2.811111 | 2.779165 |
| r2-B | 252/270 | 270 | 0 / 0 | 2.800000 | 2.767575 |
| r2-A | 225/270 | 270 | 0 / 0 | 2.500000 | 2.471361 |
| r3-A | 225/270 | 270 | 0 / 0 | 2.500000 | 2.471299 |
| r3-B | 253/270 | 270 | 0 / 0 | 2.811111 | 2.778928 |

The last column is explicitly supplementary: successes divided by last completion minus arm start. The older global plan includes drain, while E11's frozen experiment protocol specifies90s. These are different denominators, not interchangeable capacities.

### Arrival cohorts and actual delayed requests

Cohorts use **scheduled offer arrival** in pre[0,15), slow[15,45), recovery[45,90), with denominators15/30/45s. Both scheduling lag and actual-send lag satisfy100ms. Target traffic shares instead use backend-dispatch timestamps. They are different event definitions, even where counts coincide.

| Arm | Pre SLO / offers | Slow SLO / offers | Recovery SLO / offers | Target dispatches pre / slow / recovery | Actually delayed |
| --- | --- | --- | --- | --- | --- |
| r1-A | 45/45 | 45/90 | 135/135 | 23/45; 45/90; 67/135 | 45 |
| r1-B | 45/45 | 73/90 | 135/135 | 23/45; 17/90; 64/135 | 17 |
| r2-B | 45/45 | 72/90 | 135/135 | 22/45; 18/90; 61/135 | 18 |
| r2-A | 45/45 | 45/90 | 135/135 | 22/45; 45/90; 68/135 | 45 |
| r3-A | 45/45 | 45/90 | 135/135 | 23/45; 45/90; 67/135 | 45 |
| r3-B | 45/45 | 73/90 | 135/135 | 23/45; 17/90; 64/135 | 17 |

All pre/recovery cohorts meet SLO (3req/s). A's slow cohort is45/90 (1.5req/s); B is73/90,72/90,73/90 (2.433333,2.4,2.433333req/s). Injection-cohort membership does not mean exposure: only requests selected for the target whose **shim receipt** lies in the interval incur delay. There are90 slow-cohort offers per arm but45 delayed in A and17/18/17 in B. The remainder go to the unaffected replica. No actually delayed request meets the full SLO. An already-selected delay may finish after45s; interval end is not instantaneous completion of every in-flight delay.

The E12-requested slowdown-cohort comparison emphasizes the treatment interval and yields a larger per-second difference than the frozen whole-arm primary. Both are disclosed; the primary is not retroactively changed. The retained E11 preparation handoff is incomplete as evidence of any earlier primary-metric wording, so this audit does not invent such provenance.

### Detection, observed routing recovery and client recovery

| Pair | Whole B−A (/s) | Slow-cohort B−A (/s) | Detect after 15s | Healthy after 45s | Target after Healthy | Client recovery: first send / completion after 45s |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.311111 | 0.933333 | 4.600847s | 5.304344s | 60/119 | 0.001824s / 1.308374s |
| 2 | 0.300000 | 0.900000 | 4.272906s | 7.310288s | 57/113 | 2.000160s / 3.307458s |
| 3 | 0.311111 | 0.933333 | 4.608048s | 5.311956s | 60/119 | 0.001618s / 1.308631s |

B target share during slowdown falls from A's45/90 to17/90,18/90,17/90. After declared Healthy, target shares60/119,57/113,60/119 demonstrate resumed near-even routing, not merely a health check or state flag. Every B arm has exactly Healthy→Degraded→Healthy, with zero false degradations or extra degrade/restore cycles; A applies no lifecycle transition.

Client latency recovery is separate: the table reports the first **post45s target request** whose subsequent target-request suffix all satisfies SLO, using actual send and completion. This retrospective descriptive definition is not a new gate or an exact continuously observed recovery instant. B's suffix contains64/61/64 requests; the first successful response completes1.308374/3.307458/1.308631s after injection end, before the detector accumulates four completions and restores state at5.304344/7.310288/5.311956s. All135 post-end scheduled offers per arm meet SLO. Sparse target traffic bounds what can be observed between requests.

The benefit comes from avoiding some slow-path assignments, not making injected requests fast. Residual delayed requests still violate SLO. Claims apply only to this workload, rate, delay and policy. E11 observed slowdown restoration **does not establish E10 crash recovery**; E10 remains censored at60s.

## Verification and discrepancies

- Full archive hash matched the retained sibling `.sha256` **before** analysis. Safe unique canonical member paths, no links/special members,92 inventoried files and exactly93 regular archive members (inventory excluded from itself); no missing/unexpected files or digest mismatch. Tar header sizes equal actual bytes read. The historical inventory contains hashes, **not independent byte sizes**; new size measurements are not misrepresented as historical metadata.
- Independent stdlib code imports no harness/analyzer. It checks source freeze, six unique arms/three pairs,270 unique canonical IDs each, recorded token-ID shapes and hashes, exact paired content/schedules, A/B configuration identity apart from enabled flag/ephemeral shim endpoints, observed injection and timing, client stream receipt fields/usage, latency arithmetic and SLOs.
- Every reservation/prefill release/final release is replayed into an independent work/count ledger; admission caps, matching dispatch, nonnegative balances and zero terminal accounting hold. Observer identities/timings match dispatch/requests; applied threshold transitions are checked from observations. Sampled waiting is zero, health responsive, preemption/cache-hit counters unchanged, metric gaps≤1s. Sampling cannot rule out events between samples.
- Calibration60/60 meets SLO and TTFT<200ms. Paired pilot225/270 vs253/270, with valid reduced target share and one restored cycle, precedes measurements. Timestamp ordering confirms calibration→pilot A→pilot B→catalog. Retained CPU preparation logs report12 E11 +37 E10 +64 gateway tests and a valid fake pair; those logs are outside the measured archive and are not authenticated by its checksum. Their fake-pair monotonic end precedes calibration. CPU results are not GPU observations.
- Maximum recorded scheduling lag across measured arms is3.581ms; injection marker deviation≤1.699ms; actual send/delay checks also meet the frozen100ms limit. An initial new audit assertion incorrectly equated task scheduling and actual send within1ms; source inspection showed separate timestamps. It was replaced by checking both against the frozen100ms gate. That failed audit output is preserved separately; no historical row changed.
- Runtime logs contain `EngineDeadError` only **after shutdown begins**, plus a shutdown semaphore warning. No pre-shutdown ERROR/traceback is present. SIGTERM teardown records cover both owned backends; post-run listener inventory matches pre-run ports53/41565, port8001 absent; recorded GPU process list empty and memory1MiB each. These are historical teardown observations, not a new GPU workload.
- Raw SSE payloads/per-token timestamps were not retained. Complete streams are verified from content/DONE/length/usage/error receipt fields, not byte replay. Prompt token IDs and server usage agree; this audit does not retokenize with newly loaded dependencies.
- Publication discrepancy: the executed commit was preparation-only, but the reviewed source already had an execution report; the earlier preparation-only claim was stale. This closeout links and augments that report instead of duplicating implementation or modifying the historical freeze.

## Preservation

Evidence root: `/workspace/e11-results/e11-20260920T222928Z-99438da`.
Archive: `/workspace/e11-results/e11-20260920T222928Z-99438da.tar.gz`.
Retained reference: same archive path plus `.sha256`.
Archive size: **48,317,473 bytes**.
SHA-256: `de534527f6c6bb4307ed62064b1684d3740e6acc82fbe98206e21dcb463a3b98`.
New audit output: `/workspace/project/audits/` on verified volume `experiment-network-volume`.

Independent off-volume backup is **outstanding**. No authorized destination was identified in repository documentation or standard backup configuration paths; installing/creating storage or downloading locally was not authorized. Persistent-volume archive + working directory are not independent copies. GitHub contains compact reports/derived summaries/code, not the raw E11 archive.

Historical evidence stayed read-only. No inference, model loading, infrastructure change or
unrelated-process stop occurred. The public repository retains the [machine-readable summary](e11-audit-summary.json);
the large raw audit source is maintained separately.
