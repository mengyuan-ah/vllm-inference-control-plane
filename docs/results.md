# Results and claim boundaries

This page is the portfolio-level index. Detailed protocols, raw rows, corrections, and audits remain
authoritative in the linked reports.

## Performance foundation

The Phase 1 single-replica lab characterized one Qwen3-4B vLLM server on an RTX 4090:

- Best tested closed-loop output throughput: `1,402.81 output tokens/s` at concurrency 32.
- Concurrency 64 declined to `1,285.57 output tokens/s` with substantial queueing and tail latency.
- Fixed interactive workload: 1,024 input and 128 output tokens.
- Highest validated sustainable open-loop rate: `2.5 req/s`.
- Lowest validated failing rate: `3.0 req/s`.
- The first failing SLO dimensions were TPOT and end-to-end tail latency.
- A long-context concurrency-20 probe reproducibly reached approximately 90% sampled KV-cache use.

These experiments show why peak aggregate throughput and interactive SLO capacity are different
operating points.

## Control-plane experiments

| Experiment | Result | Supported claim | Do not claim |
| --- | --- | --- | --- |
| E1 gateway overhead | Mean TTFT `+7.870 ms`; E2E `+8.140 ms` | Low measured overhead at 1 req/s | High-load overhead bound |
| E5 work-aware routing | Better work balance; TTFT p95 worsened by roughly 69-71 ms | Balance alone does not guarantee latency | Work-aware routing always helps |
| E6 tight limits/queue | SLO successes: loose `768/768`, cap `611/768`, cap+queue `25/768` | Over-limiting can destroy goodput | Queueing is universally harmful |
| E7 shadow prediction | `1,349/1,425` misses detected, but many healthy requests flagged | Strong detection with poor selectivity | Production-ready predictor |
| E8 admission | At 10 req/s: `63/768` versus `624/768` SLO successes | Admission helped tested sustained overload | Universal optimal threshold |
| E9 burst concentration | Mean bursty goodput gain `+1.25 req/s`; `43.75%` rejected | Benefit depended on arrival shape | Same gain for arbitrary traffic |
| E10 crash/restart | Control `540/540`; fault `534/540`; no dispatch to excluded target | Exclusion, survivor continuity, content-aware failover | Full recovery within the window |
| E11 slow replica | `675/810` to `758/810` SLO successes; all recovery cycles observed | Traffic reduction improved the tested delay workload | GPU degradation recovery in general |

## Primary sources

- [Phase 2 final synthesis](../artifacts/summaries/phase2-final-report.md)
- [E8 execution result](../artifacts/summaries/e8-execution-result.md)
- [E9 protocol and result context](../experiments/e9/README.md)
- [E10 execution report](../experiments/e10/EXECUTION_REPORT.md)
- [E11 execution report](../experiments/e11/EXECUTION_REPORT.md)
- [E11 independent audit](../artifacts/summaries/e11-audit-report.md)
- [Phase 1 final report](../artifacts/summaries/phase1-final-report.md)

## Evidence policy

The public repository retains code, frozen protocols, compact derived summaries, selected charts,
and audit reports. Large raw streams, server logs, package tarballs, repeated deployment snapshots,
and provider-specific retention inventories are preserved separately and are not required to run
the CPU demo or understand the reviewed conclusions.
