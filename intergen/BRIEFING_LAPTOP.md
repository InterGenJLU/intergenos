# InterGen Phase 2 — Briefing for claude-laptop

## What Is InterGen?

InterGen is the AI assistant that ships with InterGenOS. It lives in the GNOME panel,
knows the system inside and out, and can diagnose problems, install packages, convert
files, search the web, and assist with any operation. Text-based (no voice). Local LLM
first, cloud fallback optional.

70% of the core engine is being ported from JARVIS (66,000 lines, 100% tool calling
accuracy across 1,200+ trials) by claude-main on the Ubuntu desktop. Your job is the
InterGenOS-specific pieces that don't need JARVIS reference.

## Your Assignments

### 1. Core Tools (7 tools)

Each tool implements the `BaseTool` interface (see `intergen/interfaces/tool.py`).

| Tool | File | Safety | Description |
|------|------|--------|-------------|
| `run_command` | `tools/run_command.py` | tiered | Shell execution. read=auto, write=confirm, destructive=blocked |
| `read_file` | `tools/read_file.py` | auto | Read any file, return contents |
| `write_file` | `tools/write_file.py` | confirm | Write/edit files, show diff before confirming |
| `manage_packages` | `tools/manage_packages.py` | tiered | pkm integration. list/search=auto, install/remove=confirm |
| `manage_services` | `tools/manage_services.py` | tiered | systemctl. status=auto, start/stop/enable/disable=confirm |
| `web_search` | `tools/web_search.py` | auto | Serper API + DuckDuckGo fallback |
| `open_application` | `tools/open_application.py` | auto | Launch desktop apps via gio/xdg-open |

**Safety tiers for run_command:**
- **auto** (no confirmation): read-only commands (ls, cat, grep, df, ps, uname, etc.)
- **confirm** (user must approve): write commands (mkdir, cp, mv, chmod, chown, etc.)
- **blocked** (refused): destructive commands (rm -rf /, mkfs, dd if=/dev/zero, etc.)

Build the classifier that categorizes commands into these tiers. Err on the side of
caution — if uncertain, classify as "confirm."

### 2. Hardware Detector (`intergen/hardware.py`)

Detect system capabilities and assign a tier:

| Tier | RAM | GPU | Model |
|------|-----|-----|-------|
| 1 | <8 GB | None/integrated | Qwen3.5-2B Q4_K_M (~1.5 GB) |
| 2 | 8-15 GB | Any | Qwen3.5-9B Q4_K_M (~5.5 GB) |
| 3 | 16 GB+ | Discrete | Qwen3.5-35B-A3B MoE Q4_K_M (~21 GB) |

Read from: `/proc/meminfo`, `lspci` (GPU), `/sys/class/drm/*/device/vendor`.
Return a `HardwareTier` dataclass with ram_gb, gpu_vendor, gpu_model, tier, recommended_model.

### 3. Model Manager (`intergen/model_manager.py`)

- Download models from Hugging Face (Unsloth GGUFs)
- SHA256 verification after download
- Store in `/var/lib/intergen/models/llm/`
- Track downloaded models in a manifest file
- Select model based on hardware tier
- Embedding model: nomic-embed-text-v1.5 (274 MB, CPU-only)

### 4. Llama Server Manager (`intergen/llama_manager.py`)

- Start/stop llama-server as a subprocess
- Health check endpoint (GET /health)
- Auto-restart on crash (max 3 retries, then fail)
- Configure: model path, context size, GPU layers, port
- Default endpoint: localhost:8080
- Must pass `--jinja` flag for tool calling support

### 5. D-Bus Daemon Skeleton (`intergen/dbus_daemon.py`)

- Service name: `com.intergenos.InterGen`
- Interface: `com.intergenos.InterGen`
- Methods: `Ask(message: str) -> str`, `Status() -> str`, `GetTier() -> str`
- Runs as systemd user service
- This is the skeleton — claude-main will wire it to the router later

## What claude-main Is Building (Don't Touch)

- Conversation router — routes user input to the right handler
- LLM router — manages local vs cloud LLM calls
- Semantic matcher — 4-layer intent matching (regex → keyword → embedding → LLM)
- MCP client — Model Context Protocol integration
- Safety classifier — higher-level safety beyond individual tool classification
- Skill manager — skill registration and lifecycle
- Metrics/logging — event tracking
- Watchdog — health monitoring

## Interface Contracts

**READ `intergen/interfaces/` BEFORE WRITING ANY CODE.**

These files define the exact method signatures, dataclasses, and return types that
both agents build against. If you need an interface changed, post to the coordination
channel — don't modify interface files directly.

## Coordination Protocol

Read `intergen/COORDINATION.md` for the full protocol. Key points:

1. Work on branch `intergen-tools` (create from master after pulling)
2. Post status updates to the VPS coordination endpoint
3. Never edit files owned by claude-main
4. Pull master and rebase before requesting merge
5. **PROPOSE before major changes — to everyone.** Before making any significant
   design decision or architectural choice, post a proposal to the coordination
   channel AND check with the owner. This applies agent-to-agent too. If your
   proposed approach might conflict with what claude-main is building, it's better
   to discover that before writing 500 lines, not after. When in doubt, propose first.

## Testing

You have the advantage: you're ON InterGenOS. Test everything against the real system:
- `run_command` — run actual commands, verify safety classification
- `manage_packages` — test against real pkm
- `manage_services` — test against real systemctl
- `hardware.py` — test against THIS laptop's actual hardware

Write a test suite: 20 read-only commands (should auto-approve), 10 write commands
(should require confirmation), 10 destructive commands (should block).

## Getting Started

```bash
cd /mnt/intergenos
git pull origin master
git checkout -b intergen-tools
# Read the interface specs
cat intergen/interfaces/*.py
# Start building
```

## Questions?

Post to the coordination channel or ask the owner. Don't guess on interface contracts.
