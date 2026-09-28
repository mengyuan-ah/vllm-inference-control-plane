# E6 blocked run — 2026-09-13

Status: `E6_BLOCKED`

E6 stopped at measured arm 23 because final gateway accounting was invalid. The pod qualification, two-replica GPU smoke, all nine pilot arms, and 22 of 27 measured arms completed validly. The valid measured subset contains 5,632 offered requests. Arm 23 is preserved but excluded, and arms 24–27 were not run.

## Execution identity

- Runpod pod: `exclusive_chocolate_salmon` (`0p6zypku1ulre4`)
- Hardware: 2× NVIDIA GeForce RTX 4090
- Model: `Qwen/Qwen3-4B-Instruct-2507`
- Revision: `cdbee75f17c01a7cc42f958dc650907174af0554`
- vLLM: `0.25.1+cu129`; PyTorch: `2.11.0+cu129`; Transformers: `5.13.1`
- Executed repository merge: `651f7e17037c2e1ee06bb66494a9f328577af0c6`
- Uploaded source archive SHA-256: `6ac9a392c1fdda7b8215ea866ccd2cb10c001b8c97e21557ff7ff48e30f62ea8`
- Evidence root: `/workspace/e6-results/e6-20260913T2245Z-651f7e1`

Configuration A used the nonbinding safety cap of 128 requests per replica. B and C used cap 4 per replica. C additionally used an eight-request FIFO queue and a 0.5-second residence deadline. All configurations used round-robin and frozen beta `107.2820167`.

The bounded pilot completed 9/9 valid arms at 3.0, 4.5, and 6.0 requests/second. It validated the proposed fixed settings rather than searching for smaller values. The measured run used 256 offers per arm, seeds 62001–62003, paired traces within rate/repetition, and ABC/BCA/CAB order rotation.

## Blocker

Arm 23 was configuration C at 6 rps with seed 62002. Request `e6-measured-62002-0172` returned the intentional `queue_timeout` 429 after 0.500266 seconds of queue residence, but its gateway event stream contains a reservation for replica 2 and no final release. Final accounting retained one active reservation and `14756.0981376` work units.

The harness correctly failed the arm for:

- replica 2 reservation ledger did not drain;
- replica 2 work did not drain;
- request `e6-measured-62002-0172` lacked exactly one final release.

This is consistent with a queue-timeout/dispatch race. It is an accounting validity failure, so the run was stopped without retrying the arm or executing arms 24–27. The invalid arm's full trace, 256 request records, gateway events, backend samples, and summary are preserved.

## Partial valid results

These aggregates include valid arms only. Counts and specified measurement durations are summed. Latency, arrival-lag, and queue-residence percentiles are pooled from request-level records across valid repetitions; they are not averages of arm percentiles. Successful-request latency excludes rejected/expired requests, while SLO fraction uses all offers as its denominator. SLO goodput is the SLO-success count divided by the sum of the specified `count/rate` windows.

| Rate | Config | Reps | Offered | Success | Reject | Queue expiry | Unexpected | SLO goodput | SLO fraction | TTFT p95 | TPOT p95 | E2E p95 | Queue p95 | Peak queue |
| ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3.0 | A | 3 | 768 | 768 | 0 | 0 | 0 | 3.000 rps | 100.0% | 104.4 ms | 11.235 ms | 1.529 s | 0 ms | 0 |
| 3.0 | B | 3 | 768 | 768 | 0 | 0 | 0 | 3.000 rps | 100.0% | 104.0 ms | 11.235 ms | 1.529 s | 0 ms | 0 |
| 3.0 | C | 3 | 768 | 768 | 0 | 0 | 0 | 3.000 rps | 100.0% | 104.1 ms | 11.233 ms | 1.529 s | 0 ms | 0 |
| 4.5 | A | 3 | 768 | 768 | 0 | 0 | 0 | 4.500 rps | 100.0% | 105.5 ms | 11.887 ms | 1.612 s | 0 ms | 0 |
| 4.5 | B | 3 | 768 | 768 | 0 | 0 | 0 | 4.500 rps | 100.0% | 104.7 ms | 11.883 ms | 1.610 s | 0 ms | 0 |
| 4.5 | C | 3 | 768 | 768 | 0 | 0 | 0 | 4.500 rps | 100.0% | 105.5 ms | 11.884 ms | 1.612 s | 0 ms | 0 |
| 6.0 | A | 1 | 256 | 256 | 0 | 0 | 0 | 6.000 rps | 100.0% | 107.1 ms | 12.872 ms | 1.739 s | 0 ms | 0 |
| 6.0 | B | 2 | 512 | 406 | 106 | 0 | 0 | 4.758 rps | 79.3% | 105.4 ms | 11.946 ms | 1.620 s | 0 ms | 0 |
| 6.0 | C | 1 | 256 | 215 | 0 | 41 | 0 | 0.188 rps | 3.1% | 582.6 ms | 11.981 ms | 2.103 s | 482.6 ms | 4 |

The achieved-rate mean was within 0.001 rps of each configured rate, and pooled arrival-lag p95 was at most 1.085 ms in every valid group. Backend waiting stayed zero. Dispatch was balanced in complete groups.

### Per-repetition results

| Rate | Seed | Config | Success | Reject | Queue expiry | SLO count | SLO goodput | SLO fraction | TTFT p95 | TPOT p95 | E2E p95 | Queue p95 |
| ---: | ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3.0 | 62001 | A | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 104.39 ms | 11.219 ms | 1.5274 s | 0 ms |
| 3.0 | 62001 | B | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 103.71 ms | 11.234 ms | 1.5284 s | 0 ms |
| 3.0 | 62001 | C | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 103.76 ms | 11.233 ms | 1.5287 s | 0 ms |
| 3.0 | 62002 | A | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 104.72 ms | 11.239 ms | 1.5285 s | 0 ms |
| 3.0 | 62002 | B | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 104.55 ms | 11.237 ms | 1.5294 s | 0 ms |
| 3.0 | 62002 | C | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 104.41 ms | 11.232 ms | 1.5289 s | 0 ms |
| 3.0 | 62003 | A | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 103.71 ms | 11.235 ms | 1.5291 s | 0 ms |
| 3.0 | 62003 | B | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 103.76 ms | 11.236 ms | 1.5285 s | 0 ms |
| 3.0 | 62003 | C | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 103.76 ms | 11.234 ms | 1.5286 s | 0 ms |
| 4.5 | 62001 | A | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 105.33 ms | 11.884 ms | 1.6119 s | 0 ms |
| 4.5 | 62001 | B | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 104.63 ms | 11.884 ms | 1.6104 s | 0 ms |
| 4.5 | 62001 | C | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 105.86 ms | 11.882 ms | 1.6123 s | 0 ms |
| 4.5 | 62002 | A | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 104.99 ms | 11.889 ms | 1.6122 s | 0 ms |
| 4.5 | 62002 | B | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 104.93 ms | 11.890 ms | 1.6110 s | 0 ms |
| 4.5 | 62002 | C | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 105.13 ms | 11.891 ms | 1.6118 s | 0 ms |
| 4.5 | 62003 | A | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 105.90 ms | 11.885 ms | 1.6115 s | 0 ms |
| 4.5 | 62003 | B | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 104.16 ms | 11.859 ms | 1.6077 s | 0 ms |
| 4.5 | 62003 | C | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 105.55 ms | 11.880 ms | 1.6110 s | 0 ms |
| 6.0 | 62001 | A | 256 | 0 | 0 | 256 | 6.000 | 100.0% | 107.14 ms | 12.872 ms | 1.7385 s | 0 ms |
| 6.0 | 62001 | B | 204 | 52 | 0 | 204 | 4.781 | 79.7% | 105.10 ms | 11.949 ms | 1.6195 s | 0 ms |
| 6.0 | 62001 | C | 215 | 0 | 41 | 8 | 0.188 | 3.1% | 582.57 ms | 11.981 ms | 2.1030 s | 482.57 ms |
| 6.0 | 62002 | B | 202 | 54 | 0 | 202 | 4.734 | 78.9% | 105.52 ms | 11.945 ms | 1.6207 s | 0 ms |

## What the valid subset suggests

- At 3.0 and 4.5 rps, the cap and queue were inactive, and all configurations met all three SLOs for every offer.
- At 6 rps, B kept accepted-request p95 latency lower than the single valid A repetition, but rejected about 20.7% of offers. Its SLO goodput was therefore about 4.76 rps versus A's 6.0 rps in the available repetitions: the cap protected accepted latency but did not improve useful completion rate.
- The single valid C repetition absorbed some overload briefly, but waiting dominated TTFT and end-to-end latency. It expired 41 offers and only 8 of 256 met all SLOs. Under these fixed settings, the queue added excessive delay rather than useful SLO completions.
- These are partial observations, not final E6 conclusions. Rate 6 lacks the planned balanced three repetitions, and the second C repetition exposed the accounting defect.

## Preservation and teardown

The remote evidence root contains source and controller identities, exact commands, traces, request records, gateway events, backend samples, logs, summaries, checkpoint records, `PARTIAL_RESULTS.json`, `FINAL_INVENTORY.tsv`, and `FINAL_MANIFEST.sha256`. The final inventory covers 258 files with byte sizes and SHA-256 hashes.

Only E6-owned processes were stopped. Gateway port 8080 is closed; out-of-scope port 8001 remains closed as at baseline. Both vLLM replicas on 8101/8102 are healthy and idle. Preemption and prefix-cache-hit deltas remain zero. The Runpod itself was not stopped or terminated.

Before another measured execution, repair and test the queue-timeout versus dispatch-reservation race under a new source identity. Because that changes gateway semantics, do not append arms 24–27 to this run; start a new measured run after the repair.
