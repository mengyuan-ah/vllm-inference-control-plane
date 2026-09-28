# E9 equal-average smooth-versus-bursty overload

**Historical preparation below; measured execution and a prior independent CPU audit subsequently completed.** See the [preserved E9 audit](../../artifacts/summaries/e9-prior-audit.md) and [its provenance](../../artifacts/summaries/phase2-provenance.md). This closeout publishes that prior report; it does not rerun E9.

E9 is an offline-prepared four-condition experiment asking whether the frozen E8
selected-replica count cap protects request-level SLO goodput and limits backlog
under a deterministic burst shape with the same average offered load as smooth
traffic.

The measured comparison has smooth/bursty traffic crossed with permissive
configuration A/enforced configuration B. Each of three prompt-seed repetitions
contains all four conditions, for 12 arms and 3,072 offers. Every arm uses a fixed
32-second denominator. This package prepares the future GPU experiment; it does
not contain GPU results and does not authorize execution.

The E8 rule is not a general load predictor here. With round-robin routing and
this fixed workload, configuration B acts as a selected-replica count cap:
reject when the selected replica already has at least eight outstanding requests.
The rejected routing turn is consumed and is not rerouted.

Run CPU checks from this directory:

    UV_CACHE_DIR=/workspace/project/cache/uv uv run --project . --group dev pytest -q
    UV_CACHE_DIR=/workspace/project/cache/uv uv run --project . --group dev ruff check .

The frozen design is in `FROZEN_PROTOCOL.md`; the exact order is in
`ARM_CATALOG.json`; future operator commands are in `EXECUTION_COMMANDS.md`.
