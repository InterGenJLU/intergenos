# Qwen3.5 Thinking/Reasoning Mode Research
**Date:** April 14, 2026
**Purpose:** Validated behavior of thinking mode across Qwen3.5 model sizes

---

## Sources

### Unsloth — Qwen3.5 Documentation
**URL:** https://unsloth.ai/docs/models/qwen3.5

### HuggingFace Discussions
- https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/discussions/2
- https://huggingface.co/unsloth/Qwen3.5-27B-GGUF/discussions/4
- https://huggingface.co/unsloth/Qwen3.5-397B-A17B-GGUF/discussions/8

### llama.cpp Issues
- https://github.com/ggml-org/llama.cpp/issues/20182 (enable_thinking bug)
- https://github.com/ggml-org/llama.cpp/issues/13160 (Qwen 3.0 bug)

### Other
- https://www.buildmvpfast.com/blog/qwen-3-5-non-thinking-mode-local-agent-deployment-stable-2026

---

## VALIDATED Defaults by Model Size

| Model | Reasoning Default | Notes |
|-------|------------------|-------|
| Qwen3.5-0.8B | **DISABLED** | |
| Qwen3.5-2B | **DISABLED** | InterGen Tier 1 |
| Qwen3.5-4B | **DISABLED** | |
| Qwen3.5-9B | **DISABLED** | InterGen Tier 2 |
| Qwen3.5-27B | ENABLED | |
| Qwen3.5-35B-A3B | ENABLED | InterGen Tier 3 |
| Qwen3.5-122B-A10B | ENABLED | |
| Qwen3.5-397B-A17B | ENABLED | |

## How to Control

**llama-server flag:**
```bash
--chat-template-kwargs '{"enable_thinking":true}'   # enable
--chat-template-kwargs '{"enable_thinking":false}'  # disable
```

**Known bug (llama.cpp #20182):** `enable_thinking: false` may not fully disable thinking. `--reasoning-budget 0` partially works but may just hide thinking data.

**Per-request API:** Not fully working as of current llama.cpp. Server-level flag is more reliable.

**Qwen3 vs Qwen3.5:** Qwen3 supported /think and /nothink soft switches in user message. Qwen3.5 does NOT officially support this.

## BREAKTHROUGH: --reasoning off (April 14, 2026)

**Root cause of empty responses and slow inference was NOT model capability.**

The chat template's auto-detection was enabling reasoning extraction regardless of `enable_thinking` setting. llama-server's `--reasoning` flag defaults to `auto`, which detects Qwen3.5's think support and ENABLES reasoning extraction even on 2B/9B where thinking is "disabled."

The model was generating massive `reasoning_content` (1248+ words on 2B) that consumed the entire token budget, leaving nothing for actual content.

**Fix:** `--reasoning off`

### Benchmark Data (HP laptop, 16K ctx, 1 slot, same system prompt)

| Query | 2B reas ON | 2B reas OFF | 9B reas ON | 9B reas OFF |
|-------|-----------|-------------|-----------|-------------|
| Berlin Wall | 28s EMPTY | 1.8s OK | 77s OK | 6.3s OK |
| Systemd | 58s EMPTY | 5.4s OK | 87s OK | 9.8s OK |
| Python list/tuple | 58s EMPTY | 26.1s OK | 108s EMPTY | 13.1s OK |

- 2B: 15-43x speedup. All queries produce content.
- 9B: 6-12x speedup. All queries produce content. All under 15s.
- 9B with reasoning off is the clear winner for Tier 2 CPU-only.

### Additional flags validated from source code

| Flag | Default | Issue | Fix |
|------|---------|-------|-----|
| `--parallel` | 1 in common.h, **overridden to 4 by server.cpp** | 4x KV cache waste | `--parallel 1` |
| `--ctx-size` | 0 (model's full context, up to 262K) | Massive KV allocation | `--ctx-size 16384` |
| `--reasoning` | auto (enables on Qwen3.5) | Reasoning bloat | `--reasoning off` |
| `--cache-type-k/v` | F16 | Could save memory with Q8_0 | Not yet tested |

### InterGen launch flags (validated)
```
llama-server --model MODEL --port 8080 --ctx-size 16384 --n-gpu-layers 999 --parallel 1 --reasoning off --jinja
```

## Lessons Learned

1. "2B has reasoning disabled by default" was TRUE at the model level but FALSE at the server level. The server's auto-detection overrode the model's default. **Validate the full stack, not just one layer.**

2. "The laptop can't handle 9B" was an assumption based on data collected with wrong server flags. One configuration fix (--reasoning off) changed 77-108s broken responses to 6-13s reliable responses. **"Known issue" is not an acceptable status.**

3. Empty responses were attributed to "model limitation" without investigating server logs. The actual error was "Context size exceeded" — a configuration problem, not a capability problem. **Read the logs before writing a diagnosis.**

**Rule:** Validate against documentation AND runtime behavior before writing fixes. "This looks like X" is not "I verified this is X."
