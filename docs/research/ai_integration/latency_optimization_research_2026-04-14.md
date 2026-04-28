# InterGen Latency Optimization Research
**Date:** April 14, 2026
**Purpose:** Strategies to keep response latency acceptable for real users

---

## Sources

### OpenAI Latency Optimization Guide
**URL:** https://developers.openai.com/api/docs/guides/latency-optimization
- Streaming is the single most effective UX approach (cuts perceived wait to 1-2s)
- Cutting 50% of output tokens cuts ~50% of latency
- Smaller/faster models where full capability isn't needed

### Fastest Small LLMs for Inference 2026
**URL:** https://www.siliconflow.com/articles/en/fastest-small-LLMs-for-inference
- Model size is the main factor in inference speed
- Smaller models can outperform larger ones when used correctly

### LLM Inference: Prefill, Decode, KV Cache Guide
**URL:** https://www.morphllm.com/llm-inference
- Prefill is compute-bound (GEMM), decode is memory-bound (GEMV)
- KV cache compression: 2-7x faster depending on context

### llama.cpp KV Cache Quantization
**URL:** https://github.com/ggml-org/llama.cpp/discussions/20969
- turbo3: 468 t/s vs f16's 63 t/s at pp=16K (7.4x faster)
- Compressed KV fits in L2 cache during attention

### Speculative Decoding
**URL:** https://arxiv.org/html/2508.08192v1
- Small draft model generates 3-12 candidate tokens
- Target model verifies in one parallel forward pass
- Can double throughput

### Edge LLM Survey
**URL:** https://www.sciopen.com/article/10.26599/TST.2025.9010166
- CPU-only: ~1.4 tok/s for 70B, higher for smaller models
- Hybrid CPU+GPU improves to ~2.3 tok/s

---

## InterGen Latency Strategy (7 layers)

| Layer | Technique | Expected Latency | Effort |
|-------|-----------|-----------------|--------|
| 1 | Expand fast path (keyword+template) | 0-10ms | Low |
| 2 | Streaming responses | 1-2s perceived | Low |
| 3 | System state cache (polling) | 0ms from cache | Medium |
| 4 | Speculative execution (tool+LLM parallel) | Saves tool wait | Medium |
| 5 | Smaller model for synthesis | 2-5x faster | Low |
| 6 | Output token budgeting (cap max_tokens) | ~50% reduction | Low |
| 7 | KV cache warming (--cache-prompt) | Saves prefill | Medium |

## System State Cache Design

### Near-static (poll every 5 min)
hostname, kernel, OS release, CPU, GPU, installed packages, enabled services, block devices, mounts, network interfaces

### Slowly changing (poll every 30-60s)
disk usage, memory usage, uptime, service status, load average

### Never cached (always live)
process list, network connections, log tail, file contents

### Pattern
Background thread → run commands → store in dict → template synthesis reads from cache → 0ms response from data ≤60s old

### Prior-art precedent
Filesystem metadata caching on polling cycle — proven pattern, directly applicable
