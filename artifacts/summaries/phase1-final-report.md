# vLLM Performance Lab — Final Technical Report

## 1. Executive summary

This single-GPU lab characterized a fixed vLLM deployment from basic service
validation through concurrency, sequence-length, open-loop SLO capacity, and
KV-cache pressure experiments.

The server used an NVIDIA RTX 4090 24 GB, Python 3.12,
`Qwen/Qwen3-4B-Instruct-2507`, vLLM `0.25.1+cu129`, PyTorch
`2.11.0+cu129`, CUDA 12.9, maximum model length 8,192, tensor and pipeline
parallel size 1, GPU-memory utilization 0.90, and seed 42.

The central result is that serving capacity depends on the objective:

- The best tested closed-loop throughput was **1,402.81 output tokens/s at
  concurrency 32**.
- The validated open-loop interactive-SLO capacity was only **2.5–3.0
  requests/s** for a 1,024-input/128-output workload.
- At 3.0 requests/s, request delivery and reliability remained sound, but TPOT
  and end-to-end tail latency failed.
- vLLM reported a **96,128-token KV cache**, implemented as **6,008 blocks of
  16 tokens**. A long-context concurrency-20 workload reproducibly reached
  approximately **90.1–90.3% maximum sampled KV usage** without OOM.

These findings demonstrate the trade among throughput, latency, scheduler
pressure, and active KV-cache demand. They do not identify a kernel, compute,
HBM, or memory-bandwidth bottleneck because no kernel profiling or hardware
counter experiment was performed.

## 2. Lab goal and fixed environment

The lab was designed for a senior backend engineer developing practical LLM
inference-infrastructure skills: operating vLLM, designing controlled
experiments, measuring streaming latency, reasoning about continuous batching,
and preserving reproducible evidence.

| Component | Fixed value |
|---|---|
| GPU | NVIDIA RTX 4090, 24 GB |
| Model | `Qwen/Qwen3-4B-Instruct-2507` |
| Python | 3.12 |
| vLLM | `0.25.1+cu129` |
| PyTorch/CUDA | `2.11.0+cu129` / CUDA 12.9 |
| Maximum model length | 8,192 |
| Tensor / pipeline parallel | 1 / 1 |
| GPU-memory utilization | 0.90 |
| Server seed | 42 |
| API | `http://127.0.0.1:8000/v1` |

The API remained localhost-only. Project Python and vLLM executables came from
`.venv`. Persistent caches were stored under `.cache/`.

## 3. Measurement definitions and controls

- **TTFT**, directly measured where timestamps were retained: first non-empty
  streamed text minus actual HTTP start.
- **TPOT**, derived: post-first-token completion time divided by
  `output_tokens - 1`.
- **ITL**, directly observed between streamed text events. Network chunks can
  coalesce token events, so TPOT is the more stable aggregate decode-cadence
  metric in this lab.
- **E2E latency**, directly measured or reconstructed from saved streaming
  timing: completion minus actual HTTP start.
- **Output throughput**, derived: completed output tokens divided by the
  measured run duration.
- **Open-loop delivery**, directly timed and derived in Day 5b: request count
  divided by the later of the last scheduled offset and last actual-start
  offset. Response drain is excluded.
- **Sampled telemetry** includes GPU utilization, active KV-cache fraction, and
  running/waiting request gauges. Sampled maxima can miss short transients.

Warm-ups and pilots were excluded. Prompt and output lengths were
tokenizer-verified, distinct deterministic seeds avoided shared prefixes, and
server configuration was held constant within controlled experiments.

## 4. Day-by-day design

| Phase | Question | Design |
|---|---|---|
| Day 1 | Does the fixed server work reproducibly? | API, real completion, streaming smoke test, metrics, environment capture, c=1 pilot |
| Day 2 | What is the single-request baseline? | c=1, 128/32 and 1,024/128 synthetic workloads |
| Day 3 | How does continuous batching scale? | Closed-loop concurrency 1, 2, 4, 8, 16, 32, 64 at 1,024/128 |
| Day 4 | How do input and output length affect latency? | Input sweep 128–4,096; output sweep 32–512 at c=1 |
| Day 5/5b | What rate satisfies an interactive SLO? | Normalized exponential open-loop arrivals; refined boundary repetitions |
| Day 6 | How does live token demand pressure KV capacity? | Long-context ladder, c=20 pressure repeats, matched-token geometry |

## 5. Baseline service behavior

Day 2 validated 16/16 measured requests for each case:

| Workload | TTFT p95 | TPOT p95 | E2E p95 | Official output throughput |
|---|---:|---:|---:|---:|
| 128 input / 32 output | 24.35 ms | 9.352 ms/token | 313.69 ms | 102.68 tok/s |
| 1,024 input / 128 output | 32.67 ms | 9.466 ms/token | 1,233.56 ms | 104.05 tok/s |

Within Day 2, the longer prompt primarily increased prefill-sensitive TTFT;
decode cadence remained similar. These two workloads demonstrate the expected
direction but do not form a full prompt-length scaling curve.

## 6. Continuous batching and concurrency scaling

Day 3 held the 1,024/128 workload fixed:

| Concurrency | Output tok/s | TTFT p95 | TPOT p95 | E2E p95 |
|---:|---:|---:|---:|---:|
| 1 | 100.81 | 75.77 ms | 9.480 ms | 1.278 s |
| 8 | 691.12 | 82.27 ms | 11.210 ms | 1.505 s |
| 16 | 988.07 | 439.87 ms | 17.557 ms | 2.484 s |
| 32 | **1,402.81** | 764.34 ms | 26.382 ms | 4.026 s |
| 64 | 1,285.57 | 3,055.54 ms | 46.574 ms | 8.904 s |

Throughput scaled strongly through c=32 even though sampled GPU utilization
was already high at low concurrency. Higher concurrency allowed vLLM to batch
more useful work per scheduling step. This is why a high utilization sample
alone does not prove efficient serving or saturation.

Latency degradation and explicit queueing began between c=8 and c=16. At c=64,
throughput declined 8.36% from c=32 while tail latency grew sharply. The tested
throughput maximum is bracketed by c=32 and c=64; intermediate values such as
40, 48, and 56 were not tested, so the exact maximizing concurrency is unknown.

## 7. Prompt-length and output-length effects

Day 4 is the cleaner controlled sequence-length baseline.

- Increasing input length from 128 to 4,096 tokens increased mean TTFT from
  24.11 to 277.16 ms, approximately 11.5×.
- Mean TPOT rose more modestly from 9.369 to 10.061 ms/token.
- Increasing output length from 32 to 512 tokens with the prompt fixed near
  1,024 kept mean TTFT near 70–71 ms while mean E2E grew from 0.362 to 4.936 s.

Prompt length primarily changes prefill work, but longer context can also
modestly increase decode cost because each decode step attends over a larger
KV cache. Position-bucket ITL evidence supports this interpretation, but
without kernel profiling or HBM counters it does not directly prove a hardware
bottleneck.

Cross-day TTFT comparison is not controlled: Day 2's 1,024/128 TTFT was much
lower than Day 4's corresponding value, plausibly due to warm state, caching,
or server-state differences. Conclusions therefore come from within-Day-4
comparisons.

## 8. Interactive SLO capacity

The fixed Day 5b SLO for 1,024 input and 128 output tokens was:

- p95 TTFT ≤ 250 ms
- p95 TPOT ≤ 15 ms/token
- p95 E2E ≤ 2.0 s
- 100% success and exact token counts
- actual request-start delivery ≥95% of offered rate

Day 5 used a normalized/rescaled exponential schedule but historically labeled
completion throughput including final drain as achieved rate. Day 5b preserved
that arrival process and corrected the primary delivery metric:

```text
request_count /
max(last_scheduled_offset, last_actual_start_offset)
```

| Boundary | Pooled requests | Delivery | TTFT p95 | TPOT p95 | E2E p95 | Decision |
|---|---:|---:|---:|---:|---:|---|
| 2.5 req/s | 384 | ≥0.99998 per run | 144.95 ms | 14.366 ms | 1.921 s | sustainable |
| 3.0 req/s | 384 | ≥0.99997 per run | 150.42 ms | 16.445 ms | 2.182 s | failing |

Every 2.5-request/s repetition passed; every 3.0-request/s repetition failed
TPOT and E2E. TTFT, delivery, reliability, and exact-token criteria continued
to pass. Arrival-lag p95 remained below 2.49 ms, maximum client in-flight was
19, and sustained explicit waiting did not precede latency failure.

Capacity was therefore latency-limited, not delivery- or reliability-limited.
At 2.5 requests/s the mean output throughput was 311.45 tok/s, 22.2% of the
Day 3 c=32 point. Closed-loop peak throughput and open-loop interactive-SLO
capacity answer different operational questions.

## 9. KV cache, memory pressure, and PagedAttention

The server reported:

| Quantity | Value |
|---|---:|
| GPU KV-cache capacity | 96,128 tokens |
| Block size | 16 tokens |
| GPU blocks | 6,008 |
| KV dtype | BF16 |
| Model layers / KV heads / head dimension | 36 / 8 / 128 |

The theoretical tensor storage per cached token is derived as:

```text
2 (K and V) × 36 layers × 8 KV heads × 128 dimensions × 2 bytes
= 147,456 bytes/token
= 144 KiB/token
```

This reconciles to approximately 13.2 GiB for 96,128 tokens. CUDA graphs,
weights, activations, allocator metadata, and other runtime allocations are
separate.

For the 4,096/256 long-context workload, c=20 produced maximum sampled KV usage
of 90.195%, 90.062%, and 90.261% in three runs. c=24 was correctly skipped.
The scheduler contained pressure without OOM or preemption.

Process-allocated GPU memory remained about 23.2 GiB because vLLM reserves its
working allocation. This is not active KV occupancy. The active KV gauge
changed with live tokens even while process allocation remained flat.

PagedAttention maps logical sequence blocks to non-contiguous physical
fixed-size blocks. Block rounding reduces external fragmentation compared with
contiguous full-sequence allocation, but it does not remove final-block slack,
metadata, or all allocator overhead.

The matched-token comparison held logical demand at 69,632 tokens:

| Shape | Mean max sampled KV | Mean output tok/s | Mean TTFT p95 | Mean TPOT p95 |
|---|---:|---:|---:|---:|
| 16 × 4,096/256 | 72.24% | 439.71 | 4.169 s | 32.555 ms |
| 64 × 1,024/64 | 71.49% | 802.14 | 3.799 s | 68.017 ms |

Similar occupancy supports token/block-driven allocation. Different sequence
counts, prefill lengths, decode stream counts, and scheduler interactions
explain why equal aggregate tokens do not imply equal latency or throughput.

## 10. Cross-day synthesis

Continuous batching raises aggregate throughput by sharing scheduling and GPU
work across active sequences, but it also creates interference. Prefill can
delay decode, more decode streams lengthen scheduling cycles, and queueing can
grow once admission or cache pressure appears.

Latency can fail before sustained explicit waiting because requests may already
be admitted and actively contending for scheduling and decode service. A zero
waiting gauge means no request was sampled in that scheduler state; it does not
mean every active request satisfies its latency target.

The experiments expose three different capacity views:

1. Single-request latency and decode cadence.
2. Closed-loop throughput under fixed concurrency.
3. Open-loop sustainable arrival rate under an explicit latency SLO.

None can substitute for the others.

## 11. Operational and experimental lessons

- Pin the exact CUDA-specific vLLM wheel; a CUDA 13 wheel failed on CUDA 12.9.
- Use `.venv/bin/python` and `.venv/bin/vllm` explicitly.
- Verify PID identity rather than trusting PID files.
- Separate request-start delivery from completion throughput including drain.
- Preserve failed attempts and deviations instead of overwriting them.
- Measure streaming timestamps and exact server-reported token usage.
- Treat telemetry extrema as sampled and maintain raw evidence.
- Run a safety pilot before long or pressure-sensitive work.

## 12. Limitations and unsupported claims

- One GPU, model, vLLM build, and server configuration were tested.
- Synthetic prompts and deterministic output do not represent every production
  workload.
- Finite runs do not prove indefinite steady-state stability.
- Cross-day comparisons are descriptive when warm state, scripts, or sampling
  differ.
- Intermediate concurrency/rate/length values were not exhaustively tested.
- Network effects beyond the local Pod were excluded.
- Aggregate metrics do not reveal exact block tables or fragmentation.
- No profiling evidence supports a kernel-, compute-, HBM-, or
  memory-bandwidth-bottleneck claim.

## 13. Reproduction guide

1. Follow `README.md` for the exact CUDA 12.9 environment and localhost-only
   server.
2. Inspect each `configs/day*.env` and corresponding script before execution.
3. Never run all experiments automatically; each day has pilots, stopping
   rules, and confirmation requirements.
4. Use `results/day*/commands.md` and the historical task specifications for
   exact methodology.
5. Preserve new attempts under distinct names and validate raw outputs before
   publishing conclusions.

## 14. Interview-ready conclusions

- “Continuous batching improved throughput through c=32, but tail latency and
  queueing grew; c=64 lost throughput, so maximum utilization was not maximum
  useful work.”
- “My interactive capacity was 2.5–3.0 req/s under a defined SLO, far below the
  1,402.81 tok/s closed-loop peak because the objectives differ.”
- “Prompt length mainly increased prefill-sensitive TTFT; output length mainly
  increased total decode time. Longer context modestly raised TPOT.”
- “The process held roughly 23.2 GiB while active KV occupancy varied. Reserved
  process memory is not live KV usage.”
- “The cache held 96,128 tokens in 6,008 16-token blocks. Equal aggregate token
  demand yielded similar occupancy but different scheduling performance.”

## 15. Artifact index

- Environment and smoke test: `results/day1/`
- Single-request baseline: `results/day2/`
- Concurrency sweep: `results/day3/`
- Length sweeps: `results/day4/`
- Initial SLO sweep: `results/day5/`
- Refined SLO bracket: `results/day5b/`
- KV-cache experiment: `results/day6/`
- Final validation, manifest, review, and checks: `results/day7/`
- Interview preparation: `INTERVIEW_BRIEF.md`
- Shutdown status: `SHUTDOWN_CHECKLIST.md`
