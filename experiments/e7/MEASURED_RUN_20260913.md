# E7 shadow request-latency prediction — measured run 2026-09-13

Status: `E7_COMPLETE_READY_FOR_REVIEW`

E7 completed a bounded calibration pilot and all nine frozen measured arms. The dataset contains
enough misses (1,425/2,304) to evaluate prediction. A selected-replica count threshold caught most
misses, but at overloaded rates it also flagged most of the few passing requests. The additional
outstanding-work threshold produced exactly the same decisions, so it added no information beyond
request count for this homogeneous workload.

## Identity and qualification

- Base/result lineage: E6 result `85fb03a3d1d80944d3a2cea6c235f9115e1782df` and executed
  E7 source `2c36697` (`Prepare E7 shadow latency prediction`).
- Pod: `exclusive_chocolate_salmon` (`0p6zypku1ulre4`), 2x RTX 4090.
- Model: `Qwen/Qwen3-4B-Instruct-2507`, revision
  `cdbee75f17c01a7cc42f958dc650907174af0554`.
- Runtime: Python 3.12.13, vLLM 0.25.1, Transformers 5.13.1, NumPy 2.2.6,
  aiohttp 3.14.1.
- Executed source archive SHA-256:
  `d0421c732c33a2e3b95e6a69faad160a5ac541b6adaad9c71fad18657b1b8813`.
- E6 archive was verified unchanged before traffic; SHA-256:
  `fb2ae5b7b89465d6b0fad859e627ab0e9421fe884ca39be43ac2591264c8952f`.

Ports 8001 and 8080 were closed before E7. Replicas 8101/8102 returned HTTP 200 and were idle.
The exact pinned model paths and one process per GPU matched E6.

## What was predicted

After round-robin selected a replica, the tracker atomically captured that replica's request count
and outstanding-work ledger immediately before reserving the new request. The gateway then scored
the request before response preparation and backend dispatch. It made no metrics call and the
scores could not affect routing, admission, dispatch, retry, or release.

All measured requests received three shadow decisions:

- Always pass.
- Miss if selected-replica in-flight-before >= 8.
- Miss if selected-replica outstanding-work-before >= 109,856.7851008.

The parameters were selected from four whole calibration traces (384 rows) by maximum balanced
accuracy, tie-breaking on fewer false positives and then the lower threshold. The parameter file
SHA-256 is `5714caa86d88aa37852c408cb8cb836b2a49abd4c80f9a1b1e350d6523ca38f7`.
Measured seeds 72001/72002/72003 were held out.

## Calibration and frozen evaluation

The smoke (8 requests at 6 req/s) was valid. Calibration stopped after four of five permitted arms
because safe informative regions had been found: 6 and 8 req/s had 0/96 misses, 10 had 75/96, and
11 had 79/96. There were no unexpected failures or infrastructure-invalid arms.

Before measured traffic, rates 8/10/11, seeds, 256 requests per arm, rotation, service/workload
configuration, parameters, source hash, and pooled aggregation were written to
`FROZEN_MEASURED.json` (SHA-256
`a1e4732ccabc64c838cf106a6092004836fca0225f21b39b54f140b4d8faee6c`).

Each rate below pools 768 request rows across three repetitions. Percentiles are recomputed from
pooled successful-request samples, not averaged from arm percentiles.

| Rate | Achieved range | Success | SLO misses | TTFT misses | TPOT misses | E2E misses | TTFT p50/p95 ms | TPOT p50/p95 ms | E2E p50/p95 s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 8.005-8.006 | 768 | 0 | 0 | 0 | 0 | 103.9/111.6 | 14.134/14.169 | 1.899/1.907 |
| 10 | 10.008-10.009 | 768 | 702 | 0 | 702 | 702 | 106.6/116.0 | 16.070/16.105 | 2.147/2.156 |
| 11 | 11.009-11.010 | 768 | 723 | 0 | 718 | 720 | 107.7/116.5 | 16.670/17.086 | 2.226/2.281 |

Arrival-lag p95 ranged from 1.06 to 1.58 ms. Every arm dispatched exactly 128 requests to each
replica. Maximum sampled backend waiting, preemption delta, and prefix-cache-hit delta were zero.

## Prediction results

Both threshold predictors had identical confusion counts on every measured request.

| Scope | Predictor | TP | FP | FN | TN | Precision | Recall | False-positive rate |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| All rates | Always pass | 0 | 0 | 1,425 | 879 | undefined | 0.0% | 0.0% |
| All rates | Count threshold | 1,349 | 91 | 76 | 788 | 93.7% | 94.7% | 10.4% |
| All rates | Work threshold | 1,349 | 91 | 76 | 788 | 93.7% | 94.7% | 10.4% |
| 8 req/s | Count/work | 0 | 0 | 0 | 768 | undefined | undefined | 0.0% |
| 10 req/s | Count/work | 672 | 48 | 30 | 18 | 93.3% | 95.7% | 72.7% |
| 11 req/s | Count/work | 677 | 43 | 46 | 2 | 94.0% | 93.6% | 95.6% |

Per-run miss counts were exactly 0 at 8, 234 at 10, and 241 at 11 in each repetition. Count-rule
true positives were 224/224/224 at 10 and 226/225/226 at 11; false positives were 16/16/16 at 10
and 14/15/14 at 11. Thus the aggregate did not hide material run-to-run instability.

Prediction computation cost was 7.710 microseconds p50 and 9.647 microseconds p95 overall. This is
direct timing of the pure in-process scorer; E7 did not run a prediction-disabled GPU arm, so it
does not claim a causal end-to-end overhead estimate.

## Interpretation

The count threshold clearly beat always-pass at detecting slow requests: it caught 1,349 of 1,425
misses, while always-pass caught none. However, this is primarily a load-region indicator. Once
the system was at 10 or 11 req/s, it incorrectly flagged 91 of the 111 requests that still passed.
That makes within-overload selectivity weak despite high overall precision, because misses were
already very prevalent. Overall accuracy alone would be misleading.

The work rule made exactly the same 2,304 decisions as the count rule. For fixed 1,024/128 work,
E4's outstanding-work accounting provided no measured benefit beyond current outstanding count.

An enforced E8 experiment is not justified from this result alone. Shadow prediction cannot show
that rejection improves SLO goodput because rejection changes future load. More importantly, the
present threshold would reject most healthy requests in the overloaded arms. A new held-out shadow
study around the sharp 8-to-10 req/s transition, or a more selective pre-dispatch signal, should
precede enforcement.

## Evidence, tests, and teardown

- Evidence root: `/workspace/e7-results/e7-2c36697-20260913`
- Evidence archive: `/workspace/e7-results/e7-2c36697-20260913.tar.gz`
- Evidence archive SHA-256:
  `42d069601f0e88de556c7b89c3ba597884111b1739f2eeb6bf40a947006ed1a7`
- Archive size: 770,817 bytes.
- Inventory SHA-256:
  `1b655e563114edba77554599117f25a0cf51b54bc8dcfd4e3815f0754b7b4880`.
- Local complete suite: 61 passed; Ruff format/check passed; Python compilation passed.
- On-pod focused E7 tests: 8 passed. The legacy gateway suite was not rerun successfully on-pod
  because the image has an unrelated top-level `tests` package import collision; it passed locally
  as part of the 61-test complete suite.

All nine arms drained to zero queue, reservations, in-flight count, and outstanding work. The
in-process per-arm gateways and harnesses exited normally. Port 8001 remained closed, port 8080 was
closed, and only the two pre-existing replica processes remained. Both replicas were left running,
healthy and idle at 0% GPU utilization; the paid pod remains active and was not stopped or changed.

`E7_COMPLETE_READY_FOR_REVIEW`
