# Copy/Paste Prompt for InterGenOS-Claude

Paste this into a new Claude Code session on the HP laptop:

---

We're starting InterGen Phase 2 — building the AI assistant for InterGenOS. This is a coordinated multi-agent development effort. You're one of two Claude Code agents working in parallel.

**Before doing anything else, read these files in order:**

1. `git pull origin master` (new commits are waiting)
2. Read `intergen/COORDINATION.md` — the full multi-agent protocol
3. Read `intergen/BRIEFING_LAPTOP.md` — your specific assignments
4. Read all files in `intergen/interfaces/` — these are the interface contracts you build against

**What's happening:**
- claude-main (Ubuntu desktop) is porting the JARVIS core engine (conversation router, LLM router, semantic matcher, MCP client, safety classifier)
- You (claude-laptop, HP laptop) are building the InterGenOS-specific pieces: 7 core tools, hardware detector, model manager, llama server manager, D-Bus daemon skeleton
- You each work on separate git branches (`intergen-port` and `intergen-tools`) with strict file ownership to prevent conflicts
- There's a VPS coordination endpoint for real-time status exchange between agents
- Interface specs on master are the contracts — both agents build to them

**Your branch:** `intergen-tools` (create it from master after pulling)

**Your assignments (summary):**
1. 7 core tools implementing BaseTool (run_command, read_file, write_file, manage_packages, manage_services, web_search, open_application)
2. Hardware detector (RAM + GPU → tier 1/2/3)
3. Model manager (download from HuggingFace, SHA256 verify, tier selection)
4. Llama server manager (subprocess lifecycle, health check, auto-restart)
5. D-Bus daemon skeleton (com.intergenos.InterGen)

**Critical rules for this phase:**
- PROPOSE before major changes — to me AND to claude-main via the coordination channel
- Never edit files owned by claude-main (router, llm, semantic, mcp, safety, skills, metrics, watchdog)
- Post status updates to the VPS coordination endpoint when you start/finish/block on tasks
- Interface specs are contracts — if you need one changed, request it via coordination channel
- Test everything against THIS laptop's real system (that's your advantage)

**After reading everything, create a context carryover document** capturing the full state of this briefing, the interface specs, your assignments, and the coordination protocol. Then we'll compact your session to give you maximum room to work.

Read the files first, then tell me what questions you have before we begin.

---
