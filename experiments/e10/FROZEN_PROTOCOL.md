# E10 frozen offline protocol

## Question and scope

E10 asks whether the two-replica gateway detects and excludes one hard-failed replica,
continues through the survivor, and admits the restarted replica gradually. Healthy-but-slow
behavior is deferred to E11. E8 prediction enforcement is disabled. One pre-content failover
is enabled; no request may be retried after the first nonempty content is forwarded.

## Frozen system

- Two independent vLLM replicas, TP=1/PP=1, one GPU each, round robin.
- `Qwen/Qwen3-4B-Instruct-2507` revision
  `cdbee75f17c01a7cc42f958dc650907174af0554`.
- Prompts are constructed with the pinned tokenizer and re-encoded to verify exactly 1,024 input tokens before traffic; 128 output tokens, temperature 0, ignore EOS, seed 42.
- Beta `107.2820167`; prefix caching disabled; gateway queue disabled.
- Prediction admission disabled; retry limit one and only before content.
- TTFT <= 250 ms, TPOT <= 15 ms/token, E2E <= 2 seconds.
- Health interval 250 ms, timeout 100 ms, two failures to exclude, two successes to
  HalfOpen, one inference probe at a time, recovery headroom one request / 20,000 work,
  and four successful normal recovery requests to Healthy. Any probe failure or passive/
  active failure in Recovering returns to Unhealthy.

## Paired design and rate qualification

There are three paired repetitions and six measured arms. The catalog counterbalances
control/fault order and targets replica1, replica2, replica1. The offer count is derived as offered rate times the fixed 60-second window; an explicit conflicting count is an error. Within a pair, request IDs,
prompts, settings, scheduled offsets, offered rate, and 60-second window are identical.
Pilot seed 100101 tests one replica at 2.0, 2.5, then 3.0 req/s, at most once each. Select
the highest rate with at least 0.99 request-level SLO success and zero failed/rejected offers;
stop on the first failure. Failure at 2.0 blocks measured execution. Freeze the selected rate
and regenerate/sign the catalog before any measured traffic.

The measured timeline is: 0-10 s pre-fault; SIGKILL at 10 s; restart invocation at 25 s;
25-60 s bounded recovery observation; then at most 120 s drain. Client per-attempt timeout is
5 s, active health timeout 100 ms, inference probe timeout 30 s on GPU, server startup timeout
180 s, and overall arm timeout 360 s. A replica not fully Healthy by 60 s is censored and
reported, not granted an extension.

## Fault ownership and state semantics

Only a registered process-group leader may be signaled. Its PID, PGID, `/proc` start ticks,
exact argv, GPU, ports, generation, and launch command are rechecked immediately before
SIGKILL. Broad name matching is forbidden. Restart uses the identical command and a new
generation record. The injector records scheduled and actual times but never edits gateway
state. Control arms emit matching timeline markers without signaling.

Healthy serves normally. Unhealthy and HalfOpen are excluded. HalfOpen permits only the
separate readiness probe. A complete probe enters Recovering; normal traffic then uses reduced
headroom until four successes. `/health` success alone never restores serving. Every transition
records cause and monotonic time. All tasks cancel during gateway cleanup.

## Outcomes, metrics, and validity

Every offer is exactly one of: success without retry, success after one pre-content failover,
failed before content, failed/truncated after content, timed out, rejected for no serving
replica, rejected by safety bounds, or deliberately client-cancelled. HTTP 200 is insufficient:
completion requires content, finish reason, usage with the requested length, and `[DONE]`.
Attempts have separate IDs while one logical request ID and one offered denominator are kept.

Report scheduled versus actual fault/restart markers; observed endpoint loss; fault-to-detect
and detect-to-exclude; failed-target attempts before/after exclusion; affected offers; failover;
midstream non-replay; survivor continuity; restart-to-health, health-to-probe, Recovering and
Healthy times; oscillation; per-phase outcomes by scheduled arrival; arrival lag; TTFT/TPOT/E2E;
SLO goodput over the fixed window; per-replica dispatch/outstanding; probe traffic; and final
accounting. Counters are differenced only within a process generation. Missing recovery is a
censored outcome.

The planned kill, attributable connection/stream errors, failover, transitions, restart, and
failure to recover are outcomes. Invalidity is limited to wrong ownership/target, survivor or
gateway crash, identity/config mismatch, corrupt evidence, extra injection, material scheduling
failure, accounting/lifecycle invariant failure, or inability to restore owned processes safely.
