# E9 evidence audit

Status: `E9_EVIDENCE_VERIFIED`

## Integrity and identity

- Archive: `/workspace/e9-results/e9-measured-20260918T0455Z-a6d7f33.tar.gz`
- Archive bytes: `26108362`
- Archive SHA-256: `b6fadad6a818b2e65f136886d67f442697f0bb881a361c9bfa62a45e05465f32` (matches expected)
- Archive regular files: `73`; inventory entries: `72`; inventory excludes itself: `True`
- Duplicate members: `0`; unsafe members: `0`; hash mismatches: `0`
- Existing evidence directory matches the archived inventory: `True`
- Executed source: `a6d7f33070e27a3fd1e3518b5a97ad3c6808a973`; clean isolated experiment checkout
- Frozen catalog SHA-256: `e9b473f45787828489f2019e91498f67e3215e2c4dee6db2d5e53cc08ad0be27`

## Execution design and validity

- Frozen model/revision, two TP=1/PP=1 replicas, seed 42, max model length 8192, and disabled prefix caching: verified from commands and logs.
- CPU preflight: verified; configuration B rejects the threshold-crossing request.
- GPU smoke: 4/4 valid. Pilot: 4/4 valid. Their 49-file inventory verifies.
- Measured arms: 12/12 present and valid; catalog order matches both order indices and recorded UTC start timestamps.
- Every arm has 256 rows, 256 unique request IDs, complete decision coverage, both-replica dispatch, zero scorer errors, zero failures/timeouts/safety rejections, zero preemption/cache-hit deltas, bounded arrival lag, and zero final accounting.
- Request content is identical across all four conditions within each repetition. A/B scheduled traces match for each traffic pattern and seed.

## Independently recomputed result

- Smooth B-A goodput values: `[0.0, 0.0, 0.0]`; mean `0.000000` req/s.
- Bursty B-A goodput values: `[1.21875, 1.25, 1.28125]`; mean `1.250000` req/s.
- The interaction values equal `[1.21875, 1.25, 1.28125]`.
- All recomputed contrasts match `e9-analysis.json`.

The frozen admission rule had no observed goodput effect under smooth arrivals and improved SLO goodput by a mean 1.25 req/s under this deterministic burst pattern. This is evidence for this workload and burst shape, not a general claim about arbitrary burstiness.

## Limitations

- Schedules are deterministic and do not provide independent arrival-pattern samples.
- Repetitions vary prompts and execution conditions; inference applies only to this burst shape.
- Quiet intervals contain no probes and do not establish exact continuous time-to-SLO recovery.
- The archive and audit outputs share the same network volume and are not independent backups.

`E9_EVIDENCE_VERIFIED`
