# InterGen Development — Multi-Agent Coordination Protocol

## Agents

| Agent | Machine | Role | Branch |
|-------|---------|------|--------|
| **claude-main** | Ubuntu desktop (192.168.1.199) | Core engine port from JARVIS | `intergen-port` |
| **claude-laptop** | HP laptop (192.168.1.192) | InterGenOS-specific tools + testing | `intergen-tools` |

## Communication Channel

**VPS coordination endpoint:** `https://intergenstudios.com/intergenos/coordination.php`

**Read status (GET):**
```bash
curl -s "https://intergenstudios.com/intergenos/coordination.php?key=SECRET"
curl -s "https://intergenstudios.com/intergenos/coordination.php?key=SECRET&agent=claude-laptop"
curl -s "https://intergenstudios.com/intergenos/coordination.php?key=SECRET&last=5"
```

**Post update (POST):**
```bash
curl -s -X POST "https://intergenstudios.com/intergenos/coordination.php" \
  -d "key=SECRET" \
  -d "agent=claude-laptop" \
  -d "status=working|completed|blocked|interface-change" \
  -d "task=description of what you're doing" \
  -d "notes=details, interface changes, questions"
```

**Secret:** `5fa686d359dc87496f7f3f262ec18e3b31ebefd3000c7035ad02d6494e612bf1`

**Status values:**
- `working` — actively coding this task
- `completed` — task done, committed to branch
- `blocked` — need input from owner or other agent
- `interface-change` — CRITICAL: a shared interface was modified, other agent must read

## Rules

1. **READ the coordination channel before starting work.** Always.
2. **POST when you start a task, finish a task, or change an interface.**
3. **Never edit files owned by the other agent.** If you need a change, post to coordination channel.
4. **Interface specs in `intergen/interfaces/` are the contracts.** Any change requires an `interface-change` post.
5. **Pull master before branching. Rebase before merge.**
6. **Only owner (InterGenJLU) merges branches to master.** Agents commit to their own branches only.
7. **All existing project rules apply:** PROPOSE → WAIT → PERMISSION → CHANGE, GLASSWING, etc.
8. **PROPOSE before major changes — to EVERYONE.** This applies agent-to-agent, not just agent-to-owner. Before making any significant design decision, architectural change, or interface modification, post a proposal to the coordination channel AND check with the owner. This minimizes wasted effort — if your proposed approach conflicts with what the other agent is building, it's better to discover that before writing 500 lines, not after. When in doubt, propose first.

## File Ownership

### claude-main owns (branch: `intergen-port`):
- `intergen/router.py` — conversation router (ported from JARVIS)
- `intergen/llm.py` — LLM router (local + cloud fallback)
- `intergen/semantic.py` — semantic matcher (4-layer)
- `intergen/mcp_client.py` — MCP client
- `intergen/safety.py` — safety classifier
- `intergen/skills.py` — skill manager
- `intergen/metrics.py` — event logger + metrics
- `intergen/watchdog.py` — health monitoring
- `intergen/interfaces/` — interface spec files (shared contract)

### claude-laptop owns (branch: `intergen-tools`):
- `intergen/tools/run_command.py` — shell command execution (tiered safety)
- `intergen/tools/read_file.py` — file reading
- `intergen/tools/write_file.py` — file writing (diff confirmation)
- `intergen/tools/manage_packages.py` — pkm integration
- `intergen/tools/manage_services.py` — systemctl integration
- `intergen/tools/web_search.py` — web search
- `intergen/tools/open_application.py` — desktop app launcher
- `intergen/hardware.py` — hardware tier detection (RAM + GPU)
- `intergen/model_manager.py` — model download, verify, tier selection
- `intergen/llama_manager.py` — llama-server subprocess lifecycle
- `intergen/dbus_daemon.py` — D-Bus service skeleton

### Shared (committed to master before branching):
- `intergen/__init__.py`
- `intergen/interfaces/` — interface definitions (owned by claude-main, read by both)
- `intergen/COORDINATION.md` — this file

## Workflow

```
1. claude-main writes interface specs → commits to master
2. Both agents pull master, create their branches
3. Work in parallel on owned files only
4. Post status updates to coordination channel
5. When a module is complete → post "completed" with summary
6. Owner reviews and merges branches to master
7. Both agents pull updated master, rebase their branches
8. Repeat until Phase 2 is complete
```

## Merge Checklist

Before requesting merge:
- [ ] All owned files committed to branch
- [ ] No imports of files owned by the other agent that don't exist yet
- [ ] Interface contracts honored (check `intergen/interfaces/`)
- [ ] Posted `completed` to coordination channel with summary
- [ ] `git pull origin master && git rebase master` clean
