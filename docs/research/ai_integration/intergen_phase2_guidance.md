# InterGen Phase 2 — Development Guidance

## What We're Building

InterGen is the AI assistant that ships with InterGenOS. He lives in the GNOME panel, knows the system inside and out, and assists with any operation. Text-based, local LLM first, cloud fallback optional. 70% of the core engine is ported from JARVIS.

## Development Model

Two Claude Code agents working in parallel on separate git branches:

| Agent | Machine | Branch | Scope |
|-------|---------|--------|-------|
| claude-main | Ubuntu desktop | `intergen-port` | Core engine port from JARVIS |
| claude-laptop | HP laptop | `intergen-tools` | InterGenOS-specific tools + testing |

### Why This Works
- **claude-main** has access to the JARVIS source (`/home/christopher/jarvis`) — 66,000 lines of proven patterns
- **claude-laptop** has the running InterGenOS install — real system to test against
- Interface specs on master define the contracts between modules
- VPS coordination channel for real-time status exchange
- File ownership rules eliminate merge conflicts entirely

## Coordination Protocol

**Full protocol:** `intergen/COORDINATION.md` in the repo

**Key points:**
1. Interface specs are the law — both agents build to them
2. Separate branches, separate files, zero overlap
3. VPS coordination endpoint for status updates
4. PROPOSE before major changes — to everyone
5. Only InterGenJLU merges branches to master

## Interface Specs

**Location:** `intergen/interfaces/` — 8 files, 1,012 lines

| File | Defines |
|------|---------|
| `types.py` | Shared dataclasses: Message, ToolCall, ToolResult, HardwareTier, RouteResult, etc. |
| `tool.py` | BaseTool ABC — every tool implements this |
| `llm.py` | LLMInterface — local + cloud streaming with tool calling |
| `router.py` | RouterInterface — 8-priority conversation routing |
| `semantic.py` | SemanticMatcherInterface — 4-layer intent matching |
| `mcp.py` | MCPClientInterface + GlasswingGuardInterface — MCP + security |
| `cloud.py` | CloudProviderAdapter + EscalationManagerInterface — "Phone a Friend" |
| `hardware.py` | HardwareDetectorInterface, ModelManagerInterface, LlamaManagerInterface |
| `dbus.py` | InterGenDBusInterface — D-Bus service contract |

## Phase Timeline

| Phase | What | Owner | Est. |
|-------|------|-------|------|
| 2 | Core engine + tools | Both agents | 4-5 days |
| 3 | D-Bus daemon + model mgmt | claude-laptop leads | 4 days |
| 4 | GTK4 chat panel | TBD | 5 days |
| 5 | GNOME Shell extension | TBD | 3 days |
| 6 | MCP + Glasswing security | claude-main leads | 3 days |
| 7 | Testing + polish | Both agents | 3 days |

## How to Verify Progress

1. Check coordination channel: `curl -s "https://intergenstudios.com/intergenos/coordination.php?key=SECRET&last=10"`
2. Check git branches: `git branch -a`
3. Check VPS session log for summaries
4. Both agents post summaries when completing major modules
