# InterGen AI Assistant — Semantic Matching & Architecture Research

**Date:** 2026-04-09
**Sources:** JARVIS codebase analysis + web research (2025-2026 state of the art)

## Executive Summary

InterGen's architecture should follow a **4-layer hybrid routing pipeline** combining
deterministic pattern matching, embedding-based semantic routing, LLM tool calling,
and LLM free response. This is the proven pattern from JARVIS, validated by current
industry best practices (Semantic Router, vLLM Router, Red Hat sysadmin agents).

The key insight: **semantic matching isn't used for ALL routing — it's the fallback for
the ~5% of queries that don't match patterns or keywords.** This hybrid approach gets
95% deterministic performance with 5% semantic flexibility.

---

## Recommended Architecture

```
User Query
    |
    v
[Layer 0: Keyword Fast-Path] ---- regex/keyword match -----> Skill Handler
    |                                                          (< 1ms)
    | (no match)
    v
[Layer 1: Semantic Router] ---- embedding similarity -------> Skill Handler
    |                           (all-MiniLM-L6-v2)             (10-50ms)
    | (below threshold)
    v
[Layer 2: LLM Tool Calling] --- Qwen via llama.cpp ---------> Tool Executor
    |                           (tool schemas pruned            (1-5s)
    |                            by semantic match)
    | (no tool selected)
    v
[Layer 3: LLM Free Response] -- Qwen generates text --------> Direct answer
                                                               (1-3s)
```

---

## Component Selection

| Component | Choice | Why |
|-----------|--------|-----|
| Embedding model | all-MiniLM-L6-v2 (22M params) | CPU-friendly, 5K+ sent/sec, 80% accuracy |
| LLM | Qwen3-8B Q4_K_M via llama.cpp | Best tool calling at size, native llama.cpp support |
| Vector storage | sqlite-vec | Zero dependencies, pure C, SQLite-native |
| Tool calling | OpenAI-compatible via llama.cpp --jinja | Standard protocol, proven |
| Skill system | YAML metadata + Python handlers | Proven JARVIS pattern |

---

## JARVIS Patterns to Carry Forward

1. Pre-computed embedding cache (encode examples once at startup)
2. Hybrid 4-layer routing (pattern → keyword → semantic → LLM)
3. Per-intent thresholds (not one global threshold)
4. Negative context suppression (disambiguation without LLM)
5. Confidence-based fallback (below threshold = automatic fallthrough)
6. Tool semantic pruning (reduce LLM token overhead)
7. Priority metadata (explicit control over preference)
8. Skill metadata YAML (clean separation from handler code)

## Changes for System-Focused Assistant

1. Higher thresholds (0.88-0.95) — false routing into system commands is dangerous
2. Pattern matching dominates — sysadmin queries have predictable structure
3. Strict entity validation — paths must exist, ports must be valid
4. Full audit logging — every decision tracked
5. Read-only by default — diagnostic commands free, mutations need confirmation
6. Tool semantic pruning is critical — system tools are numerous and overlapping

---

## Key Technologies

### llama.cpp Tool Calling
- Native support via --jinja flag
- Lazy grammar: delays JSON enforcement until trigger token
- Supported models: Qwen 2.5/3, Llama 3.x, Hermes, Mistral Nemo
- OpenAI-compatible API at localhost:8080/v1/chat/completions

### Semantic Router (aurelio-labs)
- Formalized embedding-based routing into a library
- MIT licensed, pip installable
- HuggingFaceEncoder for local execution
- Reference implementation of the pattern JARVIS uses

### sqlite-vec
- Pure C SQLite extension for vector search
- Zero dependencies, runs everywhere
- Stores embeddings directly in SQLite
- Perfect for InterGenOS (no Docker, no server process)

### Small Model Tool Calling Benchmarks
- Qwen3-0.6B: 0.880 accuracy (best ultra-small)
- Qwen3-8B: excellent all-around
- Thinking mode DOESN'T help for tool selection (just adds latency)
- Q4_K_M quantization: minimal accuracy loss

---

## Safety Model

1. Diagnostic commands (read-only) run freely
2. Configuration changes show diff before applying
3. Package operations list what will change before proceeding
4. Destructive commands NEVER auto-execute
5. Output filtering: strip sensitive data before LLM sees it
6. Full audit trail: every decision logged

---

## InterGen Capabilities

### System Expert (core)
- Diagnose problems (logs, services, processes, network, disk)
- Package management via pkm
- Security auditing via Glasswing (Claude API)
- Explain configs, commands, error messages
- Performance analysis

### General Assistant
- Web search and result presentation
- File finding, content searching
- Calculations, unit conversions
- Summarize documents, explain code
- Help with unfamiliar commands

### Desktop Integration
- Open apps, manage windows (via D-Bus)
- Quick settings (volume, brightness, WiFi)
- Screenshot analysis
- Clipboard operations

---

## Sources

### Tools & Libraries
- llama.cpp function calling: https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md
- Semantic Router: https://github.com/aurelio-labs/semantic-router
- sqlite-vec: https://github.com/asg017/sqlite-vec
- sentence-transformers: https://www.sbert.net/

### Models
- Qwen3: https://qwenlm.github.io/blog/qwen3/
- all-MiniLM-L6-v2: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
- nomic-embed-text-v1.5: https://huggingface.co/nomic-ai/nomic-embed-text-v1.5
- ModernBERT: https://huggingface.co/blog/modernbert

### Prior Art
- ShellGPT: https://github.com/TheR1D/shell_gpt
- Red Hat sysadmin agents: https://github.com/rhel-lightspeed/sysadmin-agents
- Managing Linux with LLM agents: https://www.sciencedirect.com/science/article/pii/S266682702400046X
- BFCL leaderboard: https://gorilla.cs.berkeley.edu/leaderboard.html

### Architecture Patterns
- RAG vs Tool Use (2025): https://medium.com/@sumeet.pardeshi.online/rag-vs-tool-use-for-llms
- Google Agent Design Patterns: https://docs.google.com/architecture/choose-design-pattern-agentic-ai-system
- Prompt engineering for tools: https://community.openai.com/t/prompting-best-practices-for-tool-use-function-calling/1123036
