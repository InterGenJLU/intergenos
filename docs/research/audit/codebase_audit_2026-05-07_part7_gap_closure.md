# Codebase Audit — §7 Gap Closure

**Auditor:** chris-ubuntu_hplt-codium-deepseek  
**Date:** 2026-05-07 11:15–11:50 CDT  
**Master:** c4d5126  
**Paths audited:** `intergen/` (47 .py files, ~8.4k LOC), `pkm/` (8 .py files, ~2.4k LOC), `packages/ai/` (intergen + llama-cpp), `tests/` (top-level, 11 files), `plugins/`, `vm/cloud-init/`, `man/`, `data/`  
**Pre-screen:** All intergen/ and pkm/ Python files pass `python3 -m py_compile`. Zero syntax errors.  
**Findings:** 0 CRITICAL, 0 HIGH, 0 MEDIUM, 10 LOW

---

## Summary

The §7 gap closure covers paths not audited in §1-§6. The primary block (`intergen/`, `pkm/`, `packages/ai/`) is ~10.8k LOC of Python across 55 files. The intergen AI assistant runtime uses a mature 8-priority routing chain with three-tier safety classification (AUTO/CONFIRM/BLOCKED). The pkm package manager has a well-structured SQLite database with schema constraints, supersede-aware install ordering, and content-hash verification. No security-critical findings: zero hardcoded secrets, zero eval/exec usage (confirmed by grep), all subprocess calls use proper Popen with pipe setup. The secondary block (tests, plugins, cloud-init, manpages, meson-curations) is clean with minor documentation-template notes.

---

## Closure Table

| ID | Severity | Section | File:Line | Finding | Proposed Fix |
|----|----------|---------|-----------|---------|--------------|
| G1 | LOW | Intergen | `intergen/safety.py:76-87` | `_CONFIRM_COMMANDS_SINGLE` includes `python3`, `python`, `node` as confirm-tier — correct. However, command classification is prefix-based (`cmd.split()[0]`). A command like `/usr/bin/python3 script.py` (explicit path) would bypass the check because the first word is `/usr/bin/python3`, not `python3`. | Add explicit-path normalization: resolve the basename of the first word if it contains `/`. E.g., `os.path.basename(cmd.split()[0])`. |
| G2 | LOW | Intergen | `intergen/tools/web_search.py:91-99` | `_search_serper()` takes `api_key` parameter from config. If config is missing the key, the function passes `None` to `requests.post(headers={"X-API-KEY": None})`, which would produce a confusing error from the Serper API rather than a clear "API key not configured" message. | Add guard: `if not api_key: return ToolResult(error="Serper API key not configured in intergen config")`. |
| G3 | LOW | Intergen | `intergen/router.py:74` | `route()` returns `RouteResult(text="What can I help with?", ...)` for empty input. No logging of the empty-input event — this could hide a frontend bug where empty strings are repeatedly dispatched. | Add `logger.debug("Empty input received from frontend")` before the empty-input return. |
| G4 | LOW | pkm | `pkm/database.py:14` | `DB_PATH = Path("/var/lib/igos/pkm.db")` — hardcoded database path. No environment variable override. If a developer wants to test against an alternate database, they must modify source code. | Change to `Path(os.environ.get("IGOS_PKM_DB", "/var/lib/igos/pkm.db"))` for testability. |
| G5 | LOW | pkm | `pkm/installer.py:174-188` | `_extract_archive()` uses `tarfile.open()` with extraction. Unlike `igos-build/builder.py:303-353` which has `_validate_tar_members()` for path-traversal protection, the pkm installer's `_extract_archive()` does NOT validate tar members before extraction. Archives come from trusted sources (produced by igos-build), but defense-in-depth should match. | Add `_validate_tar_members()` pre-inspection or port the B3/B8 hardening from `igos-build/builder.py`. |
| G6 | LOW | Cloud-init | `vm/cloud-init/user-data:1` | Contains template placeholders `<username>`, `REPLACE_WITH_PASSWORD_HASH`, `REPLACE_WITH_SSH_PUBLIC_KEY`. These are intentional templates, not secrets. However, the file does not have a `.template` extension or README warning, so a new contributor might accidentally commit it with real credentials. | Add `vm/cloud-init/README.md` explaining template placeholders. Consider renaming to `user-data.template`. |
| G7 | LOW | Plugins | `plugins/safety-gate-v2-sketch.ts:1` | 194-line TypeScript Kilo Code plugin labeled "v2-sketch". The filename suggests work-in-progress status. The plugin implements fleet-roster validation + force-push gate, which are production-critical safety features. If "sketch" means "not yet deployed to all agents," that status should be documented. | Add a top-of-file comment block with deployment status: "DEPLOYED to SPOC workstation 2026-05-06. Awaiting DS/IGOSC/WC install." or "SKETCH — not yet deployed; use safety-gate-v1.ts for production." |
| G8 | LOW | Manpages | `man/forge.1`, `man/forge-iso.1`, `man/intergenos-first-boot.7` | Three manpages using correct troff/man macros. `forge-iso.1` lists command-line options but does not document the `--archives` flag which is required per `installer/__main__.py:138-141`. | Add `--archives DIR` option documentation to `forge-iso.1`. |
| G9 | LOW | AI packages | `packages/ai/intergen/package.yml` | intergen AI assistant package — depends on `llama-cpp` as a build dependency but does not declare Python dependency packages (`transformers`, `torch`, `sentencepiece`) that the intergen runtime likely needs. | Cross-reference intergen's `requirements.txt` (if it exists) against the package.yml dependencies. |
| G10 | LOW | Test gaps | `tests/igos_build/` | Only 2 test files (`test_parser_supersedes.py`, `test_tracker_b4_staging.py`) for igos-build. Confirmed same gap as §1 B5. The `tests/pkm/` directory has better coverage (4 files covering supersedes, verifier modes, failure paths). | Already captured in §1 B5. No additional §7 finding needed — this is a confirmation, not a new gap. |

---

## Detailed Analysis

### A. intergen/ AI Assistant Runtime (47 files, ~8.4k LOC)

**Architecture (priority-based router):**
```
P0: Compound query detection → tier-aware decomposition
P1: Keyword/regex match → direct tool dispatch  
P2: Semantic embedding match → tool dispatch
P3: LLM tool calling → tool dispatch + synthesis
P4: LLM free response (fallback)
```

**Key modules:**
- `router.py` (898 lines) — Core routing engine with 8 priorities, conversation history, memory session management
- `llm.py` (700 lines) — Local LLM integration with streaming token support and tool calling
- `safety.py` (262 lines) — Three-tier command classifier with frozenset-based whitelisting
- `memory.py` (518 lines) — Conversation memory with session management
- `dbus_daemon.py` (488 lines) — DBus daemon for system integration
- `llama_manager.py` (285 lines) — llama-server subprocess lifecycle (Popen with graceful shutdown)
- `mcp_client.py` (319 lines) — MCP server subprocess management (Popen with stdin/stdout pipes)
- `semantic.py` (278 lines) — Embedding-based semantic matching
- `intents.py` (336 lines) — Intent classification
- `model_manager.py` (287 lines) — Model download and management
- `tools/` — 20+ tool implementations (run_command, open_application, analyze_file, web_search, etc.)

**Security posture:**
- Zero `eval()`/`exec()` usage (confirmed by grep audit)
- Safety classifier covers AUTO/CONFIRM/BLOCKED tiers for common sysadmin commands
- Subprocess calls use Popen with proper pipe setup, not `shell=True`
- API keys come from config (not hardcoded) — G2
- Command classification is prefix-based — G1 (explicit-path bypass)

**Error handling:**
- 108 except blocks across intergen/ and pkm/ — comprehensive
- Empty-input handling exists but no logging (G3)

### B. pkm/ Package Manager (8 files, ~2.4k LOC)

**Architecture:**
- `database.py` (622 lines) — SQLite with schema constraints, manifest parsing, package listing, dep tracking
- `installer.py` (505 lines) — Supersede-aware archive extraction and deployment
- `repo.py` (541 lines) — Repository management
- `cli.py` (538 lines) — Command-line interface
- `verifier.py` (93 lines) — Package integrity verification with three exit codes
- `remover.py` (116 lines) — Package removal with dependency checking

**Design quality:**
- Supersede-aware install with queue ordering enforcement (RFC §4)
- Hybrid SQLite + text manifest approach for human inspection
- Content-hash verification with strict/fast modes (RFC §5a)
- Per-file SHA-256 hashing at install time (RFC v2 §2g)
- Proper SQLite transaction wrapping for atomic deploy+register

**Gaps:**
- Hardcoded DB_PATH (G4)
- Missing tar path-traversal hardening (G5)

### C. Secondary Paths

**tests/ (top-level) — 11 files:**
- `igos_build/` — 2 test files (gap: §1 B5)
- `pkm/` — 4 test files covering supersedes, verifier modes, failure paths
- `manifest/` — 1 test file for manifest phase
- `check-public-content/` — 1 test script
- `sbat/` — 1 test script
- `fixtures/` — test data

**plugins/safety-gate-v2-sketch.ts (194 lines):**
- Kilo Code plugin with fleet-roster validation + force-push gate
- "sketch" naming suggests WIP status (G7)

**vm/cloud-init/ — Ubuntu autoinstall config:**
- Template placeholders (G6)
- LVM storage layout, qemu-guest-agent, build dependencies
- Correctly configures virtiofs mount + bash as /bin/sh

**man/ — 3 manpages:**
- `forge.1` — Installer documentation (correct troff format)
- `forge-iso.1` — ISO builder (missing --archives flag, G8)
- `intergenos-first-boot.7` — First-boot greeter documentation

**data/meson-curations.yaml (227 lines):**
- Well-documented YAML format mapping meson options to igos packages
- Used by `populate-meson-db.py` for build dependency resolution

### D. packages/ai/

- `intergen/` — AI assistant runtime package
- `llama-cpp/` — llama.cpp inference engine package
- Needs cross-reference of Python deps vs package.yml (G9)

---

## Audit Techniques Applied

| Technique | Result |
|-----------|--------|
| py_compile pre-screen | All intergen/ and pkm/ Python files compile cleanly. Zero syntax errors. |
| Security scan | Zero eval/exec usage (confirmed by grep). Zero hardcoded secrets. API keys come from config. |
| Error-handling scan | 108 except blocks across intergen/ + pkm/. Good coverage. |
| Subprocess audit | All subprocess calls use Popen with proper pipe setup (`stdout=subprocess.PIPE, stderr=subprocess.PIPE`). No `shell=True` found. |
| Hardcoded-path scan | `pkm/database.py:14` — hardcoded DB_PATH (G4). Other paths are config-driven or derived from standard Linux FHS paths. |
| Test gap analysis | `tests/igos_build/` has only 2 test files (confirms §1 B5). `tests/pkm/` has 4 test files (good coverage). |
| Shell robustness | N/A — all Python. cloud-init files are YAML templates. |
| git-hygiene | cloud-init template placeholders are intentional, not secrets (G6). Safety plugin has no hardcoded agent names or secrets. |
