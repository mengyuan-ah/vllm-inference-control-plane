# E10 CPU verification

All verification ran on the existing RunPod CPU pod. No model weights, CUDA runtime, vLLM
server, GPU workload, or external inference request was used.

- E10: 6 passed.
- Gateway: 64 passed.
- E8 regression: 9 passed.
- E9 regression: 7 passed.
- Ruff lint and format checks: passed.
- Python compilation and `git diff --check`: passed.
- Owned-process CPU preflight: 54 offered/completed, zero failed/rejected, one successful
  pre-content failover, expected four-state recovery sequence, and zero final accounting.

Preflight evidence is retained at
`/workspace/project/verification/preflight/`. It is not committed because it contains
generated process identities and execution output.
