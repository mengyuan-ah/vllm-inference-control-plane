# vLLM Performance Lab — Interview Brief

## Two-minute explanation

I built a reproducible single-GPU vLLM performance lab on an RTX 4090 using
Qwen3-4B and a pinned CUDA 12.9 vLLM build. I started with streaming correctness
and a concurrency-1 baseline, then controlled concurrency, prompt/output
length, open-loop arrival rate, and KV-cache pressure.

The best tested closed-loop throughput was 1,402.81 output tokens/s at
concurrency 32, but that was not interactive capacity. With a fixed 1,024/128
workload and p95 SLOs of 250 ms TTFT, 15 ms/token TPOT, and 2 seconds E2E, the
validated open-loop capacity bracket was 2.5–3.0 requests/s. At 3.0, delivery
and reliability still passed, but TPOT and E2E failed.

I also verified a 96,128-token KV cache composed of 6,008 16-token blocks.
Concurrency 20 on a 4,096/256 workload reproducibly reached roughly 90.1–90.3%
sampled KV usage. Equal aggregate tokens produced similar cache occupancy but
different latency and throughput, showing that cache demand and scheduler
geometry are related but not equivalent.

The project taught me to distinguish measured latency from sampled telemetry,
closed-loop throughput from open-loop SLO capacity, and process-allocated
memory from active KV occupancy. I preserved raw evidence and avoided claiming
a hardware bottleneck without profiling.

## Five-minute deep-dive

1. **Controls:** fixed model, GPU, max length, server flags, exact tokens,
   streaming, warm-up exclusion, recorded seeds.
2. **Baseline:** about 102.68 and 104.05 output tok/s for 128/32 and 1,024/128
   at c=1.
3. **Concurrency:** throughput scaled to 1,402.81 tok/s at c=32, then fell 8.36%
   at c=64 while tail latency exploded.
4. **Sequence length:** prompt length drove TTFT; output length drove total
   decode time; longer context modestly increased TPOT.
5. **SLO:** corrected arrival delivery to exclude response drain; validated
   2.5 pass and 3.0 fail with three repetitions each.
6. **Memory:** derived 144 KiB/token; reconciled 96,128 tokens to about 13.2 GiB;
   reproduced the c=20 pressure point.
7. **Operational lesson:** capacity is an SLO-dependent workload property, not
   one GPU-utilization or throughput number.

## Five quantitative results

1. Day 3: **1,402.81 output tok/s at c=32**.
2. Day 3: c=64 fell to **1,285.57 tok/s**, an **8.36% decline**.
3. Day 5b: **2.5 req/s sustainable; 3.0 req/s failing**.
4. Day 5b pooled p95: 2.5 req/s **14.366 ms TPOT / 1.921 s E2E**; 3.0 req/s
   **16.445 ms TPOT / 2.182 s E2E**.
5. Day 6: **96,128 cached tokens = 6,008 × 16-token blocks**; c=20 reached
   **90.1–90.3% sampled KV usage**.

## Definitions

- **TTFT:** HTTP start to first non-empty streamed text; mostly prefill,
  admission, and scheduling before the first output.
- **TPOT:** post-first-token time divided by output tokens minus one; aggregate
  decode cadence.
- **ITL:** time between observable streamed events; chunks may coalesce tokens.
- **E2E:** HTTP start through streamed completion.
- **Open-loop load:** arrivals are scheduled independently of completions.
- **Closed-loop concurrency:** a bounded number of requests stays in flight;
  completions permit replacements.
- **Continuous batching:** the scheduler changes the active batch over time as
  requests enter, prefill, decode, and finish.
- **KV cache:** per-layer key/value tensors retained for attention over prior
  tokens.
- **Cache block:** fixed-size token allocation unit; this lab used 16 tokens.
- **PagedAttention:** maps logical sequence blocks through block tables to
  non-contiguous physical KV blocks.

## System-design questions

### How would you capacity-plan an interactive LLM endpoint?

Define workload buckets and SLOs first. Run open-loop load with scheduled versus
actual start timestamps, exact token lengths, warm-ups, and multiple boundary
repetitions. Track latency, delivery, reliability, scheduler state, and active
KV usage. Capacity is the highest rate whose every repetition and pooled
distribution meet the SLO, with safety margin added separately.

### How would you handle mixed prompt lengths?

Bucket or prioritize work by prompt/output characteristics, consider admission
control and prefill scheduling, and protect interactive decode from large
prefills. Measure mixed workloads directly; homogeneous results cannot predict
all interference.

### How would you scale beyond one GPU?

First choose whether replication, tensor parallelism, or pipeline parallelism
matches model size and traffic. Replication improves independent request
capacity; model parallelism enables larger models but adds communication.
Route based on cache locality and workload, define overload behavior, and repeat
SLO-based tests at system scale.

## Performance-debugging questions

### GPU utilization is 100%; what next?

Check useful throughput, latency, batch size, scheduler running/waiting gauges,
KV usage, power, and workload phase. High utilization can mean productive
batching, inefficient kernels, interference, or overload. Profile kernels and
hardware counters before naming the bottleneck.

### TTFT rises but TPOT is stable—what does that suggest?

Investigate prefill length, admission delay, queueing, tokenizer/client delay,
and chunked prefill. Stable TPOT suggests decode cadence is less affected than
the pre-first-token path.

### TPOT and E2E fail before waiting requests appear—how?

Admitted requests can contend inside the active batch. More prefills and decode
streams lengthen scheduling cycles even when the sampled waiting gauge is zero.
Waiting is one scheduler state, not a complete latency signal.

## Overclaims to avoid

- “100% GPU utilization proves optimal throughput.”
- “c=32 is the exact global throughput optimum.”
- “PagedAttention eliminates fragmentation.”
- “23.2 GiB process memory means 23.2 GiB of live KV.”
- “The experiment proves an HBM or compute bottleneck.”
- “A single-GPU synthetic lab establishes production capacity.”
- “Cross-day numbers form one perfectly controlled sweep.”

## Accurate resume bullets

- Built and validated a reproducible vLLM `0.25.1+cu129` single-GPU serving lab
  on an RTX 4090 with streaming latency, scheduler, GPU, and KV-cache telemetry.
- Measured continuous-batching scaling to 1,402.81 output tok/s at c=32 and
  identified throughput decline plus severe tail-latency growth at c=64.
- Established a 2.5–3.0 req/s open-loop interactive-SLO bracket using three
  boundary repetitions, pooled raw-request percentiles, and corrected
  request-start delivery semantics.
- Characterized a 96,128-token KV cache, derived its 144 KiB/token footprint,
  and reproduced approximately 90% sampled occupancy at long-context c=20.

## Follow-up learning roadmap

1. Nsight Systems/Compute profiling and HBM/SM counter interpretation.
2. Mixed prompt/output distributions and priority scheduling.
3. Prefix-caching experiments with controlled hit ratios.
4. Quantization and model-size comparisons.
5. Multi-GPU replication versus tensor parallelism.
6. Production admission control, autoscaling, and cost-per-SLO analysis.
