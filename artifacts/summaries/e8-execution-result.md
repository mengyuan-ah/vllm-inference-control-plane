# E8 execution result

E8 completed successfully on 2026-09-15 UTC against reviewed source commit
`44d2355664d90079048bc1df93d3a9db2c240117`.

## Scope and qualification

- Run ID: `e8-20260915T051921Z-44d2355`
- Hardware: 2 x NVIDIA GeForce RTX 4090
- Runtime: Python 3.12.13; vLLM 0.25.1+cu129
- Model snapshot: `Qwen--Qwen3-4B-Instruct-2507/cdbee75f17c01a7cc42f958dc650907174af0554`
- Prefix caching: disabled on both replicas
- Updated E8 CPU tests: 9 passed
- Standalone CPU fake A/B preflight: passed
- GPU smoke: passed
- Four-arm pilot gate: passed

The exact frozen source was copied to `/workspace/project`. The local and
pod source archives had matching SHA-256 digest
`7f9415aeb2a093ba9a1726480b1c2278b9113e1df0dfb16d188036a1d187eabc`.

## Pilot

All four pilot arms were valid, paired trace identity matched, decision coverage
was complete, and there were no failures, timeouts, safety rejections, telemetry
violations, or nonzero final drains.

| Rate | Arm | Offered | Accepted | Prediction rejected | SLO goodput (req/s) |
| ---: | :--- | ---: | ---: | ---: | ---: |
| 9 | A | 64 | 64 | 0 | 7.171875 |
| 9 | B | 64 | 58 | 6 | 8.156250 |
| 10 | A | 64 | 64 | 0 | 3.437500 |
| 10 | B | 64 | 52 | 12 | 8.125000 |

The pilot met the frozen gate and authorized the measured run.

## Measured result

All 18 frozen measured arms completed and passed strict analysis: three rates,
three seeds per rate, and paired A/B arms with 256 offered requests per arm.
All arms were valid. There were no failed requests, timeouts, safety rejections,
preemptions, cache hits, backend-waiting violations, arrival-lag violations, or
nonzero final drains.

| Rate | Arm | Offered | Accepted | Prediction rejected | SLO successes | SLO goodput (req/s) |
| ---: | :--- | ---: | ---: | ---: | ---: | ---: |
| 8 | A | 768 | 768 | 0 | 768 | 8.000000 |
| 8 | B | 768 | 768 | 0 | 768 | 8.000000 |
| 10 | A | 768 | 768 | 0 | 63 | 0.8203125 |
| 10 | B | 768 | 624 | 144 | 624 | 8.125000 |
| 11 | A | 768 | 768 | 0 | 47 | 0.6731771 |
| 11 | B | 768 | 570 | 198 | 570 | 8.1640625 |

Paired B-minus-A goodput differences were:

| Rate | Seed differences (req/s) | Mean difference (req/s) |
| ---: | :--- | ---: |
| 8 | 0, 0, 0 | 0 |
| 10 | 7.3046875, 7.3046875, 7.3046875 | 7.3046875 |
| 11 | 7.4765625, 7.4765625, 7.51953125 | 7.4908854 |

For rate 11, the descriptive 95% t interval was
`[7.429258880170703, 7.552511953162631]`. These results support the bounded E8
claim that enforcing the frozen count threshold preserved goodput at 8 req/s and
substantially improved request-level SLO goodput under the tested overload rates.
They do not establish generalization beyond this hardware, model, workload, and
frozen policy.

## Evidence and teardown

The complete 162-file evidence directory is retained on the independent Runpod
network volume at:

`/workspace/e8-results/e8-20260915T051921Z-44d2355`

Its compressed archive is:

`/workspace/e8-results/e8-20260915T051921Z-44d2355.tar.gz`

Archive SHA-256:

`ebe2f62253e378b3b9934026c721ca3147c720b938b8161e6a98db1edf22e3df`

The archive and checksum file are on the persistent network volume, but were not
copied off the pod before it was stopped. Both owned vLLM processes were validated
and stopped, ports 8101 and 8102 were closed, and the pod was subsequently stopped
by the operator.

One runtime deviation is recorded in the evidence: this vLLM build did not support
the documented `--disable-log-requests` option. The two failed pre-traffic launch
attempts were preserved, and the servers were relaunched with only that unsupported
option removed. No GPU traffic ran during those failed attempts.

## Independent evidence audit

A later CPU-only audit recomputed all measured results from request rows and verified
the archive, inventory, arm coverage, paired traces, request lifecycles, validity
metrics, and launch deviation. It found no discrepancy and did not run inference.
See `audit-20260916T0342Z/AUDIT_REPORT.md` and its machine-readable companion
`audit_summary.json`.
