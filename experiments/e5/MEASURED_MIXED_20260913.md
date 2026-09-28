# E5 measured mixed result - 2026-09-13

Remote evidence root:

`/workspace/e5-pilot-results/e5-measured-mixed-20260913T175355Z-97d6ef0`

Runpod pod:

- Pod name: `exclusive_chocolate_salmon`
- Pod ID: `0p6zypku1ulre4`
- GPUs: 2 x NVIDIA GeForce RTX 4090
- vLLM replicas: ports `8101` and `8102`
- Model path: `/workspace/models/Qwen--Qwen3-4B-Instruct-2507/cdbee75f17c01a7cc42f958dc650907174af0554`
- Source commit: `97d6ef0`

The run used the patched harness support for `--stage measured` and `--workload mixed`.
Evidence remained on the Runpod network volume; no large evidence or model artifact was copied
to the local workstation.

## Grid

- Workload: `mixed`
- Rates: `2.5`, `3.5`, `4.0` req/s
- Policies: `round_robin`, `work_aware`
- Seeds: `52001`, `52002`, `52003`
- Requests per arm: `256`
- Arms: `18`
- Total requests: `4608`
- Successful requests: `4608`
- Invalid arms: none

## Aggregate

| Rate | Policy | Repetitions | TTFT p95 | TPOT p95 | E2E p95 | Positive wait fraction | Max waiting | Work imbalance |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2.5 | `round_robin` | 3 | 0.355668 | 0.015155 | 6.265750 | 0.000898 | 1.0 | 0.060123 |
| 2.5 | `work_aware` | 3 | 0.424529 | 0.016459 | 6.284154 | 0.014673 | 2.0 | 0.011354 |
| 3.5 | `round_robin` | 3 | 0.372930 | 0.016709 | 6.911787 | 0.004876 | 1.0 | 0.060123 |
| 3.5 | `work_aware` | 3 | 0.442295 | 0.018051 | 6.954672 | 0.033850 | 2.0 | 0.006392 |
| 4.0 | `round_robin` | 3 | 0.384858 | 0.017791 | 7.329451 | 0.006872 | 1.0 | 0.060123 |
| 4.0 | `work_aware` | 3 | 0.456150 | 0.019985 | 7.154703 | 0.033003 | 2.0 | 0.017420 |

Work-aware minus round-robin deltas:

| Rate | TTFT p95 | TPOT p95 | E2E p95 | Work imbalance |
| ---: | ---: | ---: | ---: | ---: |
| 2.5 | +0.068861 | +0.001304 | +0.018403 | -0.048769 |
| 3.5 | +0.069364 | +0.001342 | +0.042885 | -0.053732 |
| 4.0 | +0.071292 | +0.002194 | -0.174749 | -0.042703 |

## Readout

`work_aware` reduced work imbalance across the measured mixed grid, at the cost of higher TTFT
p95 and TPOT p95. At `4.0` req/s, `work_aware` improved E2E p95 despite the latency overhead.
Backend waiting was observed and retained as a performance signal, not an infrastructure
invalidation condition; gateway queueing/rejections/failovers/health transitions/preemptions and
prefix-cache hits were absent.
