# Control-plane experiments

Each experiment is a bounded study built on the same streaming gateway. Tests run in separate
Python processes because the preserved harnesses intentionally use generic module names such as
`analyze` and `run_harness`.

| Experiment | Question | Result |
| --- | --- | --- |
| [E5](e5) | Does outstanding-work-aware routing beat round-robin? | Work balance improved, but latency did not consistently improve. |
| [E6](e6) | Do tighter headroom limits and queueing protect SLOs? | Over-tight limits reduced useful goodput; queueing performed especially poorly. |
| [E7](e7) | Can a shadow predictor identify likely SLO misses? | It detected most misses but produced too many false positives for enforcement. |
| [E8](e8) | Can enforced admission protect goodput under sustained overload? | At 10 req/s, SLO successes improved from `63/768` to `624/768`. |
| [E9](e9) | Does admission benefit depend on burst concentration? | Bursty goodput improved by `1.25 req/s` on average with a `43.75%` rejection trade-off. |
| [E10](e10) | Can the gateway safely exclude and fail over around a crashed replica? | Exclusion and survivor continuity worked; full recovery was not observed in the window. |
| [E11](e11) | Can the gateway reduce traffic to a reachable-but-slow replica and restore it? | SLO successes improved from `675/810` to `758/810`; all recovery cycles completed. |

From the repository root, run the complete CPU-only suite with:

```bash
make test
```

GPU campaigns are not CI tests. Their frozen protocols, exact environment requirements, and
evidence boundaries are documented inside each experiment directory.
