# E10 CPU-only repair verification

Date: 2026-09-19

The repair was prepared in an isolated experiment checkout.

No vLLM server, model weights, inference traffic, or GPU compute process was started during
this repair. The existing network volume remained mounted at `/workspace`.

## Repaired blockers

- Recovery work headroom is 20,000, above the frozen request work of 14,756.0981376.
- Prompts are generated with the pinned tokenizer and re-encoded to exactly 1,024 tokens.
- The harness parses SSE incrementally and records TTFT, TPOT, E2E, termination, usage, and
  request-level SLO success.
- Offer count is derived from offered rate times the fixed window; conflicting explicit counts
  fail before traffic.
- Partial evidence and final accounting are written from `finally`; pending controller tasks are
  cancelled and an experiment-owned restarted process is identity-checked before cleanup.
- Analysis rejects empty/incomplete evidence, requires six arms and three complete pairs,
  validates trace/frozen-setting identity and final accounting, and reports SLO, phase, and
  censored recovery results.

## Verification

- E10: 12 tests passed, including the actual harness against CPU fake replicas with realistic
  request work and streaming latency records.
- Gateway: 64 tests passed.
- Ruff: passed for E10 and gateway source/tests.
- Compileall: passed for E10 and gateway Python modules.
- CPU preflight: valid; 44 offered, 44 completed, zero failed/rejected, one retry, and clean final
  accounting through hard failure and recovery.
- Pinned Qwen tokenizer: generated prompt encoded and re-encoded to exactly 1,024 tokens.
- GPU process inventory: empty after verification.

GPU qualification, smoke, pilot, and measured arms remain unexecuted.
