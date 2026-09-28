# E7 post-review safety and coverage follow-up — 2026-09-13

Status: `OFFLINE_FIXES_COMPLETE_NO_GPU_RERUN_REQUIRED`

This follow-up addresses two review findings without changing E7's measured conclusions or
enabling E8 enforcement.

## Fixes

- Shadow feature validation now treats missing, non-numeric, non-finite, boolean, or negative
  count/work/timestamp inputs as invalid. Threshold decisions are recorded as undefined rather
  than raising an exception, with a per-predictor reason (`missing_features`,
  `malformed_features`, `stale_features`, or `threshold_not_configured`).
- The gateway catches unexpected scorer exceptions and records a single undefined
  `shadow_prediction` event with a `scorer_error:<ExceptionType>` reason. Scoring defects cannot
  interrupt backend dispatch or reservation cleanup; cancellation and serving errors retain their
  existing paths.
- Every confusion result now reports total requests, defined outcomes, defined predictions,
  included and excluded rows, categorized exclusion reasons, and a coverage numerator and
  denominator alongside the confusion matrix.
- Infrastructure validity, prediction-capture completeness, and measured-evaluation completeness
  are separate results. Measured arms require all expected rows, defined labels, valid Boolean
  decisions for all three predictors, and exactly one prediction event per request. Calibration
  capture still permits undefined threshold decisions before parameters are fitted.

## Preserved-row audit

The preserved archive
`/workspace/e7-results/e7-2c36697-20260913.tar.gz` was inspected read-only on network volume
`experiment-network-volume`. Its SHA-256 was verified before analysis as
`42d069601f0e88de556c7b89c3ba597884111b1739f2eeb6bf40a947006ed1a7`. No workload was
replayed and no evidence file or service was changed. The reusable command is:

```console
python audit_preserved.py /path/to/e7-2c36697-20260913.tar.gz \
  --output /new/path/E7_PRESERVED_AUDIT.json
```

The audit verified all 79 inventory entries; four calibration arms distinct from measured data;
the nine unique combinations of rates 8/10/11 and seeds 72001/72002/72003; 256 rows per arm;
the frozen predictor version and thresholds; required finite prediction-time features and
timestamps; threshold decisions; SLO labels; and one reservation, prediction, and final release
event per measured request. The separate audit result is
`audit/E7_PRESERVED_AUDIT_20260914.json`.

| Predictor | Eligible rows | Evaluated | Undefined | Coverage |
| --- | ---: | ---: | ---: | ---: |
| Always pass | 2,304 | 2,304 | 0 | 100% |
| Count threshold | 2,304 | 2,304 | 0 | 100% |
| Work threshold | 2,304 | 2,304 | 0 | 100% |

Direct recomputation reproduced 1,425 SLO misses and 879 healthy requests. Count and work each
produced TP 1,349, FP 91, FN 76, and TN 788, with identical decisions on all 2,304 rows. At
10 req/s, the rule falsely flagged 48 of 66 healthy requests; at 11 req/s, 43 of 45.

All measured comparisons therefore used the same complete request population. Saved event order
and timestamps establish feature capture before prediction and reservation-event ordering before
prediction. The evidence did not save a backend-dispatch timestamp, so raw rows alone cannot
independently establish that prediction preceded backend dispatch; the executed source establishes
that code ordering.

## Regression coverage

Offline tests reproduce the missing-timestamp case, inject an always-throwing scorer into a real
gateway integration request, verify successful streaming and zero final count/work/active-ledger
state, and cover full/partial coverage, missing events, malformed decisions, undefined reasons,
and calibration without fitted thresholds.

- Gateway: 57 tests passed; Ruff lint and format checks and Python compilation passed.
- E7: 14 tests passed; Ruff lint and format checks and Python compilation passed.

## Conclusion and limits

The original cautious conclusion remains supported. Work and count decisions being identical means
work added no predictive benefit for this fixed 1,024-input/128-output workload; it does not justify
generalizing to heterogeneous work. Shadow observations do not establish the causal effect that
rejecting flagged requests would have on useful SLO goodput, so E8 enforcement remains deferred.

If more shadow evidence is desired, a future experiment could collect fresh held-out data at finer
load points between 8 and 10 req/s. That would be a new experiment and is not executed here.
