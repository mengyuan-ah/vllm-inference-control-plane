# E10 bounded GPU execution — 2026-09-20 UTC

Executed source: `0e993a404cfe90f58b02d9dcfcc2ce156c4fc516` from the reviewed experiment checkout.
The six-arm campaign completed on two RTX 4090 GPUs. Results support failed-replica
exclusion and survivor continuity for this workload. Full return to gateway Healthy
was not observed within the frozen 60-second window in any fault arm.

## Qualification and design

All 14 Linux E10 tests, lint/format checks, byte compilation, and CPU preflight passed.
The eight-request GPU control smoke passed all SLOs. One-replica pilots at 2.0, 2.5,
and 3.0 requests/s achieved 120/120, 150/150, and 180/180 SLO successes respectively,
with zero failed or rejected offers. The selected rate was frozen at 3.0 requests/s,
180 offers per 60-second measured arm.

Model: Qwen/Qwen3-4B-Instruct-2507, revision
`cdbee75f17c01a7cc42f958dc650907174af0554`. Each replica used one GPU, TP=1/PP=1,
max model length 8192, seed 42, and prefix caching disabled. Prompts were verified
with the tokenizer as 1,024 tokens; output length was 128. SLOs were TTFT <=250 ms,
TPOT <=15 ms/token, and E2E <=2 seconds.

## Measured results

The following table is in actual catalog execution order. Each arm had 180 offers.

| Arm | SLO successes | SLO goodput (requests/s) |
|---|---:|---:|
| Repetition 1 control | 180 | 3.0000 |
| Repetition 1 fault, replica 1 | 178 | 2.9667 |
| Repetition 2 fault, replica 2 | 178 | 2.9667 |
| Repetition 2 control | 180 | 3.0000 |
| Repetition 3 control | 180 | 3.0000 |
| Repetition 3 fault, replica 1 | 178 | 2.9667 |

Controls achieved 540/540 SLO successes. Fault arms achieved 534/540 (98.89%).
Each kill interrupted two streams after content began; none of those streams was
retried. Two other requests succeeded after one pre-content failover. There were
no rejected offers and no measured dispatches to a target while excluded.

Fault-to-exclusion times were approximately 7.37 ms, 329.22 ms, and 7.61 ms.
Active health returned approximately 33.32 s, 32.87 s, and 33.17 s after restart
invocation. All three targets entered HalfOpen and then Recovering. None returned
to gateway Healthy within 60 seconds, so full recovery is censored in all three
arms. Post-arm process health checks do not extend this observation window or
establish a gateway Healthy transition.

## Evidence checks and limitations

The analyzer and additional checks passed: six arms, 1,080 measured rows with
unique request IDs within each arm, three identical full paired traces, correct
catalog order, matching prompt/token hashes, recomputed SLO counts, and zero final
reservations, outstanding work, and inflight requests. There was no survivor
exclusion or replay of interrupted streams. Maximum recorded arrival lag was
33.98 ms. Runtime logs confirmed prefix caching disabled for all five process
generations. Both processes, GPU assignments, and health were checked between arms.

The raw harness incorrectly labels the six interrupted streams as
`failed_before_content`. The derived audit uses recorded non-null TTFT to classify
them as `failed_or_truncated_after_content`; original rows and analyzer output are
preserved. Phases reflect scheduled arrival, so those requests belong to the
pre-fault arrival cohort despite being interrupted at the kill. Some reported
timeline offsets are approximate because scheduler zero is reconstructed from
request timing. Process registry generations and gateway lifecycle generations
are separate counters.

Earlier roots ending `5343ccc` and `b783734` contain failed/incomplete attempts and
must not be combined with this campaign. The `9dd858b` root was superseded before
GPU traffic. The restart lifetime, GPU binding, and generation repairs are in
merged PRs #12–#14. Earlier evidence remains preserved.

## Preserved artifacts

Network volume: `experiment-network-volume`, verified mounted at `/workspace`.

- Evidence: `/workspace/e10-results/e10-20260920T061620Z-0e993a4`
- Archive: `/workspace/e10-results/e10-20260920T061620Z-0e993a4.tar.gz`
- Archive size: **8,592,405 bytes**
- SHA-256: `371b78aea6d5f94ed0f96fb50e3e6c44fc546d56cff4a58ef6356ddefa0bf1f0`

The full archive hash and all 104 inventoried file hashes were verified directly
from the archive. There were no duplicate members, unsafe paths, missing files,
or unexpected files; the top-level inventory is separate from its listed files.
The evidence includes the campaign wrapper, audit script/results, per-arm rows,
logs, registries, frozen catalog, qualification, restoration, and cleanup records.
Detached `.sha256` and `.verification.json` files sit beside the archive.

Both final owned process groups were stopped through verified registries after
restoration checks. GPU compute-process listing was empty. The pod was left
running. No independent off-volume backup was created; retain the network volume.
This repository report does not contain the raw evidence archive.
