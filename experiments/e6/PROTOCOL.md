# E6 frozen offline protocol

## Question and configurations

E6 asks whether a per-replica outstanding-request cap and a small FIFO gateway queue improve
useful completions and latency during overload, and what rejection or waiting cost they impose.
All arms use round-robin and E4's frozen beta `107.2820167` only for accounting. Prediction,
work-aware routing, retries, and automatic client retries are disabled.

- A: per-replica request cap 128, no queue. This finite safety cap must be nonbinding.
- B: pilot-selected per-replica request cap, no queue; overload returns HTTP 429
  `queue_request_limit` immediately when neither replica has headroom.
- C: B's cap plus a pilot-selected bounded FIFO queue and residence deadline. Full queues return
  HTTP 429 `queue_request_limit`; expired entries return HTTP 429 `queue_timeout`.

`max_work=1e9` per replica and `max_queued_work=1e10` deliberately make work limits nonbinding,
so E6 isolates request-count admission. This intentionally narrows the older Section 21.7 plan,
which proposed separately exercising work headroom and queue-work exhaustion. Those are deferred:
the current objective explicitly compares request caps and a small queue with homogeneous work.

## Workload and outcomes

Every offered request has exactly 1,024 tokenizer-verified input tokens and requests exactly 128
output tokens with `ignore_eos=true`, temperature zero, and streaming usage. A success requires
HTTP 200, first content, finish reason `length`, and exact usage counts of 1,024/128. Rejections
have no expected completion-token count.

Client TTFT and E2E start at the actual gateway send, so they include queue residence. TPOT is
`(completion - first content) / 127`. Queue residence is gateway enqueue-to-dispatch time.
An offered request is an SLO success only when it is an exact success and TTFT <= 0.250 s,
TPOT <= 0.015 s/token, and E2E <= 2.0 s.

For an arm with `N` requests and configured rate `R`, the specified measurement duration is
`N/R` seconds. SLO goodput is SLO-success count divided by that duration; SLO fraction is the
same count divided by all `N` offered requests. Drain time is outside the measurement denominator.
Percentiles are empirical within-arm p50/p95 over exact successful requests. The final report will
show every repetition; any cross-arm aggregate percentile will be explicitly pooled, not averaged.

## Pilot and freeze rule

The checked-in `config/pilot.json` freezes at most nine arms, maximum 7 req/s, 180 s per arm,
and 2,400 s overall. Initial rates are 3.0, 4.5, and 6.0 req/s, with cap 4, queue size 8, and
deadline 0.5 s. Historical observations only motivate these starting points.

This pilot validates the proposed cap 4, queue size 8, and deadline 0.5 s; it does not establish
that they are the smallest suitable values. Choose three measured rates by monotone behavior: a
low point where A meets targets, the first point showing material latency degradation, and an
overload point producing cap/queue tradeoffs. If the proposed cap protects accepted latency and
the queue improves exact completions without queue waiting dominating the 2 s E2E target, freeze
the settings unchanged. If not, stop after the bounded pilot, document why they are unsuitable,
revise the protocol and pilot bounds in a new commit, and run a new declared pilot. Never adapt
settings within this pilot or select parameters by which label wins.

Measured traffic is 3 configurations x 3 rates x 3 repetitions x 256 offers = 6,912 offers.
Seeds 62001/62002/62003 are distinct from pilot seeds. Configuration order rotates ABC, BCA, CAB.
Within a rate/repetition, all configurations reuse the same trace digest and prompts. Warm-ups are
separate and excluded.

## Safety and evidence

Before each arm record port 8001 state, pod/GPU/process/runtime/model identity, commit and diff,
commands, and baseline metrics. Stop on the validity conditions in the task brief. Backend waiting,
intentional 429s, and queue delay remain outcomes. Require arrival-lag p95 <= 50 ms and no single
lag > 250 ms; otherwise stop for sustained send lag. The client timeout must exceed the queue
deadline; the default is 30 s. Any failover invalidates an arm. Any overload rejection or queue
expiration in A invalidates its required nonbinding-cap condition. The harness exits nonzero for
every invalid arm.

After every arm, fully drain and require zero queue count/work, zero inflight/active reservations,
and zero outstanding work. Preserve trace, request records, gateway events, sampled backend and
queue metrics, summary, stdout/stderr, commands, runtime identity, and final accounting. Produce a
recursive inventory with byte sizes and SHA-256 hashes before any evidence transfer.

## Commands

From `experiments/e6`, with the repository gateway importable:

```text
python run_harness.py --stage pilot --configuration A --rate 3.0 --count 64 \
  --seed 61001 --request-cap 4 --queue-size 8 --queue-deadline 0.5 \
  --tokenizer /workspace/models/Qwen--Qwen3-4B-Instruct-2507/cdbee75f17c01a7cc42f958dc650907174af0554 \
  --output-root /workspace/e6-results/PILOT_RUN_ID
```

Repeat only the frozen pilot catalog, then write the measured configuration. Measured commands use
`--stage measured --count 256`, one frozen rate, one rotated configuration, and the matching seed.
Run each configuration into a distinct output root because output roots are create-once evidence.

Required serving topology is two RTX 4090s, one full pinned-revision model replica per GPU on
8101/8102, gateway on 8080. Port 8001 is observed and left unchanged.
