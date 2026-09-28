# E6 measured admission and queue result — 2026-09-13

Status: `E6_COMPLETE_READY_FOR_REVIEW`

E6 completed all 27 measured arms and 6,912 offers. Every arm was infrastructure-valid. The
result is negative for admission limiting at the tested load: permissive round-robin admission
(A) completed every 6 rps offer within all three SLOs, while immediate admission limiting (B)
rejected about one fifth of offers and the bounded queue (C) mostly converted overload into delay
and queue expiry.

## Execution identity

- Runpod pod: `exclusive_chocolate_salmon` (`0p6zypku1ulre4`)
- Hardware: 2× NVIDIA GeForce RTX 4090
- Model: `Qwen/Qwen3-4B-Instruct-2507`
- Revision: `cdbee75f17c01a7cc42f958dc650907174af0554`
- vLLM: `0.25.1+cu129`; PyTorch: `2.11.0+cu129`; Transformers: `5.13.1`
- Executed source commit: `aa48f25` (`Fix E6 queue timeout reservation race`)
- Uploaded source archive SHA-256:
  `38b905775a039a4f0eb8c001ee26fbb9196589fcb02a8ddfded92a266cd4dfa1`
- Evidence root: `/workspace/e6-results/e6-rerun-aa48f25-20260913`
- Evidence archive: `/workspace/e6-results/e6-rerun-aa48f25-20260913.tar.gz`
- Evidence archive SHA-256:
  `fb2ae5b7b89465d6b0fad859e627ab0e9421fe884ca39be43ac2591264c8952f`

A used a nonbinding safety cap of 128 requests per replica. B and C used cap 4 per replica. C
also used an eight-request FIFO queue with a 0.5-second residence deadline. Work limits remained
nonbinding, routing was round-robin, and beta remained frozen at `107.2820167` for accounting.

The earlier nine-arm pilot had validated rates 3, 4.5, and 6 rps with the proposed settings; it
did not establish that cap 4 and queue size 8 were the smallest suitable values. After repairing
the timeout/dispatch race, a declared 64-offer C/6-rps qualification passed with 56 successes,
eight queue expirations, no unexpected failures, and empty final accounting. The measured run
then started fresh rather than combining data with the blocked run.

## Aggregate results

Each row pools request-level samples across three repetitions; percentiles are not averages of
arm percentiles. `SLO goodput` is SLO-success count divided by the sum of the three specified
`256/rate` measurement windows. `SLO fraction` uses all 768 offers as its denominator. Successful
latency and queue-residence percentiles exclude rejected and expired requests. Client TTFT and E2E
include gateway waiting.

| Rate | Config | Achieved mean | Offered | Success | Reject | Expire | SLO goodput | SLO fraction | TTFT p50/p95 | TPOT p50/p95 | E2E p50/p95 | Queue p50/p95 | Lag p95/max | Peak queue | Dispatch R1/R2 |
| ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3.0 | A | 2.999 rps | 768 | 768 | 0 | 0 | 3.000 rps | 100.0% | 97.9/104.2 ms | 11.210/11.233 ms | 1.521/1.528 s | 0/0 ms | 1.080/19.794 ms | 0 | 384/384 |
| 3.0 | B | 2.999 rps | 768 | 768 | 0 | 0 | 3.000 rps | 100.0% | 98.2/103.9 ms | 11.213/11.229 ms | 1.522/1.529 s | 0/0 ms | 1.085/20.384 ms | 0 | 384/384 |
| 3.0 | C | 2.999 rps | 768 | 768 | 0 | 0 | 3.000 rps | 100.0% | 98.3/104.0 ms | 11.216/11.233 ms | 1.522/1.529 s | 0/0 ms | 1.066/22.267 ms | 0 | 384/384 |
| 4.5 | A | 4.499 rps | 768 | 768 | 0 | 0 | 4.500 rps | 100.0% | 99.5/105.4 ms | 11.846/11.883 ms | 1.604/1.612 s | 0/0 ms | 1.074/19.899 ms | 0 | 384/384 |
| 4.5 | B | 4.499 rps | 768 | 768 | 0 | 0 | 4.500 rps | 100.0% | 99.6/105.6 ms | 11.846/11.885 ms | 1.604/1.612 s | 0/0 ms | 1.062/17.824 ms | 0 | 384/384 |
| 4.5 | C | 4.499 rps | 768 | 768 | 0 | 0 | 4.500 rps | 100.0% | 100.0/105.7 ms | 11.847/11.886 ms | 1.605/1.612 s | 0/0 ms | 1.075/20.947 ms | 0 | 384/384 |
| 6.0 | A | 5.999 rps | 768 | 768 | 0 | 0 | 6.000 rps | 100.0% | 101.2/107.3 ms | 12.480/12.889 ms | 1.687/1.740 s | 0/0 ms | 1.070/17.306 ms | 0 | 384/384 |
| 6.0 | B | 5.999 rps | 768 | 611 | 157 | 0 | 4.773 rps | 79.6% | 99.8/105.1 ms | 11.921/11.953 ms | 1.613/1.621 s | 0/0 ms | 1.065/21.122 ms | 0 | 306/305 |
| 6.0 | C | 6.000 rps | 768 | 645 | 0 | 123 | 0.195 rps | 3.3% | 478.2/580.4 ms | 11.966/11.985 ms | 1.994/2.101 s | 377.5/480.2 ms | 1.061/21.126 ms | 4 | 324/321 |

Unexpected failures and maximum backend waiting were zero in every row. All achieved-rate means
rounded to within 0.001 rps of their configured rates.

## Per-repetition results

| Rate | Seed | Config | Achieved | Success | Reject | Expire | SLO count | SLO goodput | SLO fraction | TTFT p95 | TPOT p95 | E2E p95 | Queue p95 | Peak queue | Dispatch R1/R2 |
| ---: | ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | 62001 | A | 2.999 | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 103.5 ms | 11.216 ms | 1.526 s | 0 ms | 0 | 128/128 |
| 3 | 62001 | B | 2.999 | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 104.0 ms | 11.226 ms | 1.529 s | 0 ms | 0 | 128/128 |
| 3 | 62001 | C | 2.999 | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 103.6 ms | 11.231 ms | 1.528 s | 0 ms | 0 | 128/128 |
| 3 | 62002 | A | 3.000 | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 105.3 ms | 11.234 ms | 1.529 s | 0 ms | 0 | 128/128 |
| 3 | 62002 | B | 3.000 | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 104.7 ms | 11.233 ms | 1.529 s | 0 ms | 0 | 128/128 |
| 3 | 62002 | C | 3.000 | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 103.9 ms | 11.233 ms | 1.528 s | 0 ms | 0 | 128/128 |
| 3 | 62003 | A | 2.999 | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 104.0 ms | 11.235 ms | 1.528 s | 0 ms | 0 | 128/128 |
| 3 | 62003 | B | 2.999 | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 103.8 ms | 11.229 ms | 1.528 s | 0 ms | 0 | 128/128 |
| 3 | 62003 | C | 2.999 | 256 | 0 | 0 | 256 | 3.000 | 100.0% | 104.2 ms | 11.233 ms | 1.529 s | 0 ms | 0 | 128/128 |
| 4.5 | 62001 | A | 4.499 | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 105.0 ms | 11.884 ms | 1.611 s | 0 ms | 0 | 128/128 |
| 4.5 | 62001 | B | 4.499 | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 105.3 ms | 11.882 ms | 1.612 s | 0 ms | 0 | 128/128 |
| 4.5 | 62001 | C | 4.499 | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 105.8 ms | 11.886 ms | 1.612 s | 0 ms | 0 | 128/128 |
| 4.5 | 62002 | A | 4.500 | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 105.4 ms | 11.880 ms | 1.611 s | 0 ms | 0 | 128/128 |
| 4.5 | 62002 | B | 4.500 | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 106.1 ms | 11.886 ms | 1.612 s | 0 ms | 0 | 128/128 |
| 4.5 | 62002 | C | 4.500 | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 105.4 ms | 11.887 ms | 1.612 s | 0 ms | 0 | 128/128 |
| 4.5 | 62003 | A | 4.498 | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 106.4 ms | 11.882 ms | 1.613 s | 0 ms | 0 | 128/128 |
| 4.5 | 62003 | B | 4.498 | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 105.5 ms | 11.885 ms | 1.612 s | 0 ms | 0 | 128/128 |
| 4.5 | 62003 | C | 4.498 | 256 | 0 | 0 | 256 | 4.500 | 100.0% | 105.8 ms | 11.884 ms | 1.612 s | 0 ms | 0 | 128/128 |
| 6 | 62001 | A | 5.999 | 256 | 0 | 0 | 256 | 6.000 | 100.0% | 106.8 ms | 12.897 ms | 1.740 s | 0 ms | 0 | 128/128 |
| 6 | 62001 | B | 6.000 | 204 | 52 | 0 | 204 | 4.781 | 79.7% | 105.4 ms | 11.947 ms | 1.621 s | 0 ms | 0 | 102/102 |
| 6 | 62001 | C | 6.000 | 215 | 0 | 41 | 8 | 0.188 | 3.1% | 580.5 ms | 11.980 ms | 2.101 s | 480.3 ms | 4 | 108/107 |
| 6 | 62002 | A | 6.000 | 256 | 0 | 0 | 256 | 6.000 | 100.0% | 108.4 ms | 12.889 ms | 1.740 s | 0 ms | 0 | 128/128 |
| 6 | 62002 | B | 6.000 | 203 | 53 | 0 | 203 | 4.758 | 79.3% | 105.1 ms | 11.949 ms | 1.621 s | 0 ms | 0 | 102/101 |
| 6 | 62002 | C | 6.000 | 215 | 0 | 41 | 9 | 0.211 | 3.5% | 578.5 ms | 12.001 ms | 2.099 s | 478.8 ms | 4 | 108/107 |
| 6 | 62003 | A | 5.998 | 256 | 0 | 0 | 256 | 6.000 | 100.0% | 107.1 ms | 12.886 ms | 1.738 s | 0 ms | 0 | 128/128 |
| 6 | 62003 | B | 5.998 | 204 | 52 | 0 | 204 | 4.781 | 79.7% | 105.0 ms | 11.968 ms | 1.621 s | 0 ms | 0 | 102/102 |
| 6 | 62003 | C | 5.999 | 215 | 0 | 41 | 8 | 0.188 | 3.1% | 580.8 ms | 11.984 ms | 2.101 s | 480.4 ms | 4 | 108/107 |

## Interpretation

1. **Cap and accepted latency.** At 6 rps, B's accepted-request p95 was better than A by about
   2.1 ms TTFT, 0.94 ms/token TPOT, and 119 ms E2E. The cap therefore protected accepted latency,
   but A was already comfortably within every target.
2. **Goodput versus rejection.** B rejected 157/768 offers (20.4%) and reduced SLO goodput from
   A's 6.000 to 4.773 rps. At this offered load it merely shed work; it did not improve useful
   completion rate.
3. **Queue behavior.** C dispatched 34 more requests than B and avoided immediate rejection, but
   123 offers expired. Successful requests waited 377.5 ms at p50 and 480.2 ms at p95. Only
   25/768 offers met every SLO, so the queue added excessive waiting rather than absorbing a
   sufficiently temporary burst.
4. **Costs.** A paid modestly higher accepted TPOT/E2E at 6 rps but lost no offers. B paid a 20.4%
   rejection cost for lower accepted latency. C paid both a 16.0% expiry cost and much higher
   client-visible TTFT/E2E.
5. **Uncertainty.** The conclusion is specific to two RTX 4090 replicas, the frozen model and
   homogeneous 1,024/128 workload, these open-loop rates, cap 4, and queue 8/0.5 s. The pilot did
   not find minimal limits, and higher rates, burstier arrivals, mixed work, or a shorter queue
   deadline could produce different trade-offs.

## Validity, preservation, and teardown

An independent pass rechecked all summaries and request/event records. All 27 arms had 256 records,
clean terminal streams and exact token counts for every claimed success, paired trace digests and
prompts within each rate/seed, exact ABC/BCA/CAB rotation, no failover, no unexpected failure, no
preemption or prefix-cache-hit delta, acceptable arrival lag, and empty queue/reservation/work
accounting. A had no overload outcome. The repaired former failure combination C/6/62002 passed.

The evidence root contains the qualification, exact controller scripts and logs, 27 traces, 6,912
request records, gateway events, backend samples, summaries, final aggregate JSON, final state, and
recursive inventory. `FINAL_INVENTORY.tsv` covers 205 files with byte sizes and SHA-256 hashes;
its SHA-256 is `3da2db4477b6be343e4270fe7501040b56b7dfcbafffac9b015c3adea19bfb6a`.

Only per-arm E6 gateways and harnesses were stopped. Ports 8001 and 8080 are closed, matching the
baseline; replicas on 8101/8102 return HTTP 200 and are idle at 0% GPU utilization. The existing
Runpod remains running and was not changed or terminated.
