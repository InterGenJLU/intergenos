# Plan: InterGen AI Assistant — Complete Architecture & Implementation

## Context

InterGen is the defining feature of InterGenOS — a system-focused AI assistant that lives in the GNOME panel. It knows the system inside and out, can diagnose problems, install packages, convert files, search the web, and assist with any operation. Text-based (no voice). Local LLM first, cloud fallback optional. 70% of the core engine is ported from a prior internal AI assistant project (66,000 lines, 100% tool calling accuracy across 1,200+ trials).

All research completed: semantic matching, prior-assistant capability inventory, LLM landscape (April 2026), MCP + Sentinel security, panel UI design. Research docs at `docs/research/ai_integration/`.

---

## Architecture

```
GNOME Shell Extension (panel indicator, shortcuts, dock/float, blur)
    │ D-Bus: com.intergenos.InterGen.Panel
    ▼
GTK4 Chat Panel (frameless, draggable, dockable to any edge)
    │ D-Bus: com.intergenos.InterGen
    ▼
InterGen Daemon (Python, systemd user service)
  ├── ConversationRouter (8-priority, ported from prior assistant's 18-priority)
  ├── LLMRouter (llama.cpp + quality gates + Claude fallback)
  ├── SemanticMatcher (4-layer: regex → keyword → embedding → LLM)
  ├── ToolRegistry (7 core tools, auto-discovered + MCP)
  ├── MCPBridge (ported from prior assistant, subprocess MCP servers)
  ├── SentinelGuard (MCP security: schema pinning, audit, sandboxing)
  ├── HardwareDetector (tier 1/2/3 from RAM+GPU)
  ├── ModelManager (download, SHA256 verify, tier selection)
  ├── LlamaServerManager (subprocess lifecycle, health, auto-restart)
  └── EventLogger, MetricsTracker, Watchdog
          │
          ▼
    llama-server (localhost:8080, --jinja for tool calling)
```

## Models (April 2026, Qwen3.5 family, Unsloth GGUFs)

| Tier | Model | Quant | Size | RAM |
|------|-------|-------|------|-----|
| 1 (<8GB) | Qwen3.5-2B | Q4_K_M | ~1.5 GB | ~3 GB |
| 2 (8-15GB) | Qwen3.5-9B | Q4_K_M | ~5.5 GB | ~8 GB |
| 3 (16GB+) | Qwen3.5-35B-A3B MoE | Q4_K_M | ~21 GB | ~24 GB |
| Embedding | nomic-embed-text-v1.5 | — | 274 MB | CPU |

## Core Tools (7)

| Tool | Safety | Description |
|------|--------|-------------|
| run_command | tiered (read=auto, write=confirm, destructive=blocked) | General shell execution |
| read_file | auto | Read any file |
| write_file | confirm (shows diff) | Write/edit files |
| manage_packages | list=auto, install/remove=confirm | pkm integration |
| manage_services | status=auto, start/stop=confirm | systemctl |
| web_search | auto | Serper API + DuckDuckGo fallback |
| open_application | auto | Launch desktop apps via gio |

## Semantic Matching (4-layer, ported from prior assistant)

1. **Regex/keyword** (<1ms) — catches 70-80% deterministically
2. **Embedding similarity** (10-50ms) — nomic-embed-text-v1.5, pre-computed cache
3. **LLM tool calling** (1-5s) — semantically pruned tool set
4. **LLM free response** (1-3s) — quality gates + fallback

Thresholds: 0.85-0.95 (higher than the prior assistant's 0.55-0.85 — system commands are dangerous)

## "Phone a Friend" — Claude API Escalation

InterGen knows his limits. When the local model can't deliver, he escalates to Claude.

**When InterGen escalates:**
- Quality gate failure (local model garbage after retry)
- Complex reasoning beyond local model's capability
- Sentinel security scanning (always Claude — that's the point)
- Web research synthesis (10+ pages, Tier 1/2 can't handle)
- Complex code generation / review
- Agentic multi-step tasks where local model loses coherence

**User-controlled escalation modes:**
| Mode | Behavior | Prime Directive |
|------|----------|-----------------|
| `never` | Fully offline, no API calls ever | Maximum control |
| `fallback` | Only when local fails quality gate | Transparent — user sees indicator |
| `ask` | InterGen asks "Want me to check with Claude?" | **Default** — user decides every time |
| `auto` | InterGen decides based on confidence scoring | Convenient but less transparent |

**LLM-agnostic — the user picks their provider, we provide the means:**

Pre-built provider adapters (all OpenAI-compatible API format):
| Provider | Models | Adapter |
|----------|--------|---------|
| Anthropic | Claude Sonnet/Opus | `anthropic` SDK |
| OpenAI | GPT-4o, GPT-5 | `openai` SDK |
| Google | Gemini Pro/Ultra | `google-genai` SDK |
| Mistral | Mistral Large | OpenAI-compatible endpoint |
| DeepSeek | DeepSeek V3/R1 | OpenAI-compatible endpoint |
| xAI | Grok | OpenAI-compatible endpoint |
| Custom | Any OpenAI-compatible | Base URL + API key |

**Configuration helper modal (in GTK4 panel):**
- First-run or via settings: "Set up cloud AI provider"
- Dropdown: pick provider → enter API key → test connection → save
- API key stored in GNOME Keyring (libsecret) — never plaintext
- Multiple providers can be configured (primary + fallback)
- "Custom endpoint" option for self-hosted or enterprise APIs

**Implementation:**
- Config: `api.mode: ask` (default)
- Provider config in GNOME Keyring, referenced by name
- All providers normalize to OpenAI chat completions format internally
- Visual indicator in panel: local brain icon vs. cloud icon when escalated
- Every API call logged with provider, model, escalation reason, tokens
- `intergen api usage` — shows call count, token usage per provider, cost estimate
- Confidence scoring: LLM self-rates confidence 1-5, <3 triggers escalation logic
- Provider adapter interface: `send(messages, tools) → stream[chunks]`

**The `ask` mode interaction:**
```
User: "Review this systemd unit file for security issues"
InterGen: "I can give you a basic review, but for a thorough security
           audit I'd recommend checking with Claude. Shall I?"
User: "Yes"
InterGen: [calls user's configured provider, shows cloud icon]
          "Claude identified 3 issues: ..."
```

**Configuration helper flow:**
```
InterGen: "I don't have a cloud provider configured yet.
           Would you like to set one up? It's optional —
           I work fully offline without one."
User: "Sure"
[GTK4 modal opens]
  Provider: [Anthropic ▼]
  API Key:  [sk-ant-•••••••••••••]
  Model:    [claude-sonnet-4 ▼]
  [Test Connection]  →  "✓ Connected successfully"
  [Save]
```

## MCP + Sentinel Security

- 4-tier trust: system → verified → community → untrusted
- Permission manifests in `/etc/intergen/mcp.d/*.yml`
- Schema hash pinning (rug pull detection)
- Tool description injection scanning (OWASP MCP02)
- Full audit logging to `/var/log/intergen/mcp-audit.log`
- Rate limiting per server
- Process sandboxing (systemd scope, seccomp)

## Panel UI (Hybrid: Extension + GTK4)

**Extension:** panel indicator (ECG icon, green/amber/gray), Super+I shortcut, strut management for docking, blur-behind

**GTK4 app:** frameless AdwWindow, 360×520px default, chat bubbles, GtkSourceView code blocks, ECG thinking animation, dock/float state machine

**Dock/float:** snap to any edge (struts reserve space, desktop adjusts), drag away to float, double-click header to toggle

**Colors:** brand navy (#1a1a2e, #16213e), accent sky blue (#38bdf8), Orchis-Dark compatible

## What We Port from the Prior Assistant

**Direct port (minimal changes):** tool_registry, semantic_matcher, mcp_client, event_logger, metrics_tracker, trace_context, conversation_state, tool_gate, base_skill, safety classifier

**Significant adaptation:** conversation_router (3782→~800 lines), llm_router (2083→~1200), skill_manager (995→~500), health_check (972→~400), watchdog (426→~200), web_research, context_window

**New code:** daemon (D-Bus), hardware detector, model_manager, llama_manager, persona, glasswing guard, GTK4 panel app (~800 lines), GNOME extension (~400 lines)

**Total: ~6,000 new + ~4,500 ported = ~10,500 lines**

## Build System Changes

- Add `"ai"` to `VALID_TIERS` in `parser.py:100`
- Add `"ai": 4` to `tier_priority` in `graph.py:122`
- 4 packages: llama-cpp → intergen → intergen-gnome-extension, intergen-glasswing

## Implementation Phases

| Phase | What | Effort | Depends On |
|-------|------|--------|------------|
| 1 | Build infra + llama-cpp | 3 days | — |
| 2 | Core engine (port from prior assistant) | 8 days | Phase 1 |
| 3 | D-Bus daemon + model management | 4 days | Phase 2 |
| 4 | GTK4 chat panel | 5 days | Phase 3 |
| 5 | GNOME Shell extension | 3 days | Phase 4 |
| 6 | MCP + Sentinel security | 3 days | Phase 3 |
| 7 | Testing + polish | 3 days | All |
| **Total** | | **29 days** | |

Phases 4+5 can overlap. Phase 6 can start after Phase 3.

## Key Files

| Purpose | Prior-Assistant Source | InterGen Target |
|---------|-------------|-----------------|
| Router | `core/conversation_router.py` | `intergen/router.py` |
| LLM | `core/llm_router.py` | `intergen/llm.py` |
| Tools | `core/tool_registry.py` | `intergen/tool_registry.py` |
| Semantic | `core/semantic_matcher.py` | `intergen/semantic.py` |
| MCP | `core/mcp_client.py` | `intergen/mcp_client.py` |
| Safety | `skills/system/developer_tools/_safety.py` | `intergen/safety.py` |
| Skills | `core/skill_manager.py` | `intergen/skills.py` |

## Configuration

- System: `/etc/intergen/config.yml`
- User overrides: `~/.config/intergen/config.yml`
- MCP servers: `/etc/intergen/mcp.yml`
- MCP permissions: `/etc/intergen/mcp.d/*.yml`
- Models: `/var/lib/intergen/models/{llm,embedding}/`
- Logs: `/var/log/intergen/`
- Data: `/var/lib/intergen/data/`

## Verification

1. `intergen ask "what's my disk space"` → tool call → real system data
2. `intergen status` → tier, model, health, uptime
3. 50-query semantic matching test suite → 100% routing accuracy
4. Safety gate: 20 read-only (auto), 10 destructive (confirm), 10 blocked (reject)
5. Dock panel right → desktop adjusts → drag away → floats → snap left → adjusts
6. MCP: connect test server → auto-discover → tool call → Sentinel audit logged
7. Kill llama-server → watchdog restarts → next query works
