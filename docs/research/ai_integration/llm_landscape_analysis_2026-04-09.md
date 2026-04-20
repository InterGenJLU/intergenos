# InterGen — LLM Landscape Analysis (April 2026)

**Date:** 2026-04-09
**Purpose:** Select models for InterGen's tiered local AI assistant

## Recommended Models

| Tier | Model | Quant | File Size | RAM | Tool Calling |
|------|-------|-------|-----------|-----|-------------|
| 1 (<8GB) | Qwen3.5-2B | Q4_K_M | ~1.5 GB | ~3 GB | Strong |
| 2 (8-15GB) | Qwen3.5-9B | Q4_K_M | ~5.5 GB | ~8 GB | Excellent |
| 3 (16GB+) | Qwen3.5-35B-A3B (MoE) | Q4_K_M | ~21 GB | ~24 GB | Best in class |
| Fallback 1 | Gemma 4 E2B | Q4_K_M | ~3.1 GB | ~4 GB | Good |
| Fallback 2 | Gemma 4 26B-A4B (MoE) | Q4_K_M | ~16 GB | ~18 GB | Excellent |
| Embedding | nomic-embed-text v1.5 | — | 274 MB | CPU | — |

## Key Findings

1. Qwen3.5 is the clear winner — best tool calling at every size, Apache 2.0
2. Gemma 4 (April 2, 2026) is the strongest alternative — Apache 2.0, multimodal
3. MoE models are the game-changer — 35B knowledge at 3B compute
4. Always use Unsloth GGUFs — their template fixes are required for reliable tool calling
5. Speculative decoding (0.8B draft + larger model) can 2-3x throughput
6. Q4_K_M is the practical default — <3% quality loss, best size/quality tradeoff
7. Sub-2B models CAN do tool calling but struggle with complex multi-step reasoning

## Critical: Use Unsloth GGUFs
Stock llama.cpp chat templates for Qwen3.5 have known tool calling bugs.
Unsloth has published 21 template fixes. Always download from unsloth/ on HuggingFace.

## Full analysis saved from research agent output
