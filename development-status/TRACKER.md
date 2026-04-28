# InterGenOS Development Tracker

**Living document — updated continuously as work progresses.**

**Initialized:** 2026-04-27 by chris-ubuntu-code-claude (SPOC), synthesized from 5 fleet inventory sources covering the 18-day drift window 2026-04-09 → 2026-04-27.
**Frozen historical snapshot:** [`_archive/source_of_truth_2026-04-27.md`](_archive/source_of_truth_2026-04-27.md) — initialization-time photo, do not edit.
**Purpose:** Holistic, deduped, fleet-wide picture of distro-development state. Single source of truth for prioritization and progress tracking.

---

## How to amend this tracker

Anyone in the fleet (main / laptop / windows / future contributors) can update sections in their lane. Pattern:

1. `git pull` to get latest.
2. Edit the relevant section. Keep changes focused — one logical update per commit.
3. Add a date-stamped marker to changed items: `**[2026-04-28 main]** Phase A interfaces shipped — registry + audit log + schema-pin done.`
4. Commit with a clear message referencing the section: `tracker: A1 ISO build status update`.
5. `git push`. SPOC (main) coordinates merge conflicts when they happen.

**Status markers** on items: `[OPEN]`, `[IN-PROGRESS]`, `[CLOSED]`, `[BLOCKED]`, `[DEFERRED]`. Append a date and the agent who set the marker where useful: `[CLOSED 2026-04-28 main]`.

## Per-lane primary editors

| Lane | Primary editor | Backstop |
|---|---|---|
| AI / Sentinel / `intergen` | main | laptop |
| Forge SB / installer / test-harness | laptop | main |
| Shim-review / Ethan succession / Windows-side observations | windows | main |
| Kilo plugins / fleet tooling | DeepSeek (proposes via channel) | main commits |
| Future contributor lanes | per-contributor | main / laptop |

**Anyone-can-amend backstop:** any agent can update any section if they spot drift between tracker and reality, with a date-stamped note. Primary-editor designation = responsibility for keeping the lane accurate, not exclusivity.

**Scope guard:** Excludes MCP/identity/connector/fingerprint/OAuth bug-squashing post-2026-04-22, plugin-port work (DeepSeek's lane), Cline migration retrospective (closed), plugin tooling (out of distro repo). The original 2026-04-19 MCP wrapper deploy IS in scope.

## Sources merged

| Source | Author | Window | Items | sha256 (where applicable) |
|---|---|---:|---:|---|
| `inventory_main_2026-04-27.md` | chris-ubuntu-code-claude | 2026-04-20 → 2026-04-27 (7d) | 42 | local at `/home/christopher/intergenos/development-status/` |
| `drift_2026-04-09_to_2026-04-22.md` | chris-ubuntu-code-claude (research subagent) | 2026-04-09 → 2026-04-22 (13d) | 51 + 10 DRIFT | local at `/home/christopher/intergenos/development-status/` |
| `submissions/claude-laptop/development_inventory_2026-04-27.md` | chris-intergenos-code-claude | 2026-04-22 → 2026-04-27 (7d) | 47 | `62c8e756...4c5fb04` |
| `submissions/claude-laptop/development_inventory_gap_2026-04-09_to_2026-04-22.md` | chris-intergenos-code-claude | 2026-04-09 → 2026-04-22 (13d) | 23 + 8 DRIFT-L | `2c9771ee...48962193` |
| `submissions/claude-windows/development_inventory_18day_2026-04-27.md` | chris-windows-code-claude | 2026-04-09 → 2026-04-27 (18d) | 64 + 7 DRIFT-W | `d08ff4ca...58edb91a8` |
| VPS `sessions.php` (key=...e612bf1, last=100) | endpoint archive | 2026-04-01 → 2026-04-27 | 16 entries in window | live |

**Dedup methodology:** primary attribution + cross-refs. Items surfaced by multiple agents merge to one canonical entry, attribution tag `[M]` (main) / `[L]` (laptop primary) / `[L-supp]` (laptop supplemental) / `[W]` (windows). Single-source items keep their tag for audit. Forge-SB shipped commits are heavily co-credited; tags reflect primary architect.

---

## EXECUTIVE SUMMARY

State of the 18-day drift window:

- **Plan baseline (2026-04-09 21:48Z) is partially shipped.** InterGen Phase 2 declared "complete in 4 hours vs 8-day budget" on 2026-04-14 — declaration was premature. Three structural items in the plan never built (GTK4 panel, full Glasswing, cloud adapters). Two structural items diverged from plan (tier model is functionally 4-tier not 3, priority chain compressed 8→5 with cache + identity templates).
- **Forge SB hardening landed substantially.** 146-test harness shipped feature-complete (Classes 1/2/2b/5 + Phase A-1/A-2 + post-install). Backend hardened via audit sweep (efivars, sbsign atomicity, sbsign idempotency, kernel stage re-gating). One Class deferred (Class 3 / shim-specific) by owner-acknowledged design call.
- **Three orthogonal drift axes**: intergen/ (10 DRIFT items, owner: main); Forge-SB / installer (8 DRIFT-L, owner: laptop); fleet orchestration (7 DRIFT-W, owner: windows). Total 25 unique drift findings.
- **Hard-date critical path is shim-review.** Nitrokey window 2026-04-28..05-02; Fedora advisory 2026-05-02; PR-open target 2026-05-15; MS 2011 CA hard deadline 2026-06-27. Five-week cluster.
- **Owner-architect-required items**: 5 (A11 intergen/ disposition; D5 docs scope; D8 rules-canonical-v1 arbitrations; C.W2 signing-key draft reconcile; A.W4 deferred_hardening completeness). ~~Forge GUI framework choice~~ removed — RESOLVED 2026-04-10 in `live_session_and_installer_FINAL_2026-04-10.md` (custom Forge / GTK4 + libadwaita / 6-screen flow / 833-line backend already built).
- **Voice scope RETRACTED.** Whisper.cpp / Piper TTS / espeak-ng descoped per text-only POWER RULE; doc-update needed across README/CLAUDE.md to scrub stale references.
- **Five-day distro-commit pause** between 2026-04-22 (`7da435f`) and today — fleet was on MCP/identity/Cline-arc work. That work is closed; distro lane reopens.
- **Imminent risk**: C.W2 (signing-key custody draft uncommitted) sits in Windows clone, predates final, never reconciled. Nitrokey ceremony window opens in days.

---

## PLAN BASELINE (April 9 21:48Z, archived at VPS sessions endpoint id `20260409T214829Z-ubuntu-host`)

The architecture-plan-approved language verbatim:

> **INTERGEN AI ASSISTANT — ARCHITECTURE APPROVED:**
> - 4-layer semantic matching, 7 core tools, tiered Qwen3.5 models
> - Phone a Friend (LLM-agnostic cloud escalation)
> - MCP + Glasswing security
> - Hybrid panel UI (dock/float, ECG brand signature)
> - 70% ported from JARVIS, ~10,500 lines
> - Phase 1 complete, Phase 2 (core engine port) next

This is the "what was promised" anchor. Every drift item below has its plan-language counterpart here.

**Drift origin marker — April 14 21:33Z** (`20260414T213346Z-unknown`):

> INTERGEN PHASE 2 (estimated 8 days, completed in ~4 hours): 9 core modules + 7 tools + hardware detector + model manager + llama manager + D-Bus daemon (InterGenOS-Claude). 95 unit tests + 12 integration tests, 35 behavioral test conversations.

**The drift starts here.** Phase 2 was declared complete with structural pieces silently dropped. The 8 days that should have gone to GTK4 panel + GNOME extension + full Glasswing + cloud adapters went to grader iteration (R10-R20, April 15-17). April 14 23:55Z's NEXT list still had `Forge integration, GTK4 panel` — last appearance before being silently dropped from execution.

**Quantitative receipt for the drift** (per `intergen_phase2_guidance.md`, the original Phase 2-7 timeline):

| Phase | Scope | Budget | Drift outcome |
|---|---|:---:|---|
| 2 | Core engine + tools | 4-5 days | Shipped (claimed in 4h, see drift origin) |
| 3 | D-Bus daemon + model mgmt | 4 days | Shipped |
| 4 | **GTK4 chat panel** | **5 days** | **Did not ship → DRIFT-6** |
| 5 | **GNOME Shell extension** | **3 days** | **Did not ship → DRIFT-6** |
| 6 | **MCP + Glasswing security** | **3 days** | **Stub-only → DRIFT-7** |
| 7 | Testing + polish | 3 days | R10-R28 quality iteration absorbed this 3x over |

Total budgeted: 22-25 days. Phase 2.5 (~22-26 calendar-day estimate per current plan) is essentially completing what was scoped but never built.

---

## DRIFT FROM PLAN — 25 items, three orthogonal axes

### Axis 1: intergen/ (10 items) — primary observer: main's research subagent

**Severity ordered.** Plan-said vs code-says, with reviewer impact.

#### DRIFT-1: Tier 2 CPU-only runs the 2B model, NOT 9B — split inside Tier 2 [M]
- **Plan said**: Tier 2 (8-15GB RAM) → Qwen3.5-9B Q4_K_M, ~5.5GB.
- **Code says**: Tier 2 with discrete GPU → 9B; Tier 2 CPU-only → 2B (`hardware.py:35-60, 76-79`, commit `5949b5d` 2026-04-15).
- **Why drifted**: R26-R28 latency data — 9B on CPU = 50s/query (unusable); 2B on CPU = 17s/query.
- **Reviewer impact**: Anything assuming Tier 2 == 9B (test plans, model-download manifests, sizing tables in user docs) is wrong. Hardware-detect → model-name now branches on `gpu_vendor in ("nvidia","amd")`.
- **Tier model is functionally 4-tier**, not 3.

#### DRIFT-3: Priority chain compressed 8→5 with state cache + identity templates inserted before keyword/semantic [M]
- **Plan said**: "ConversationRouter (8-priority, ported from JARVIS 18-priority)".
- **Interface spec said**: `RouterInterface — 8-priority conversation routing` (per `intergen_phase2_guidance.md` line 44 — interface contract authored alongside the plan).
- **Code says**: 5 named priorities + 4 short-circuits in `router.py:67-177`. Short-circuits are `empty_input`, **state cache lookup** (commits `3e679af` `3a8ab19` 2026-04-14), **identity templates** (30+ exact/substring matches, 0ms, commit `61da4f9` 2026-04-16), memory ops. P0=compound (tier-aware), P1=keyword/regex, P2=semantic ≥0.85, P3=LLM tool calling (gated), P4=LLM freeform.
- **Why drifted**: R10-R20 data; state cache short-circuits 70-80% of queries to <10ms with no LLM round-trip.
- **Reviewer impact**: "Identity" is now a routing tier, not a system-prompt modifier alone. Plan's 8-priority figure is misleading; actual chain is 9 functional layers. **Triple divergence**: plan said 8, interface spec said 8, code shipped 5+4. Both spec and code drifted from each other and from the plan.

#### DRIFT-6: GTK4 panel + GNOME Shell extension are NOT built [M] [L-A11]
- **Plan said**: Phase 4 (GTK4 panel, 5 days) + Phase 5 (GNOME extension, 3 days) — 8 days of UI work, ~1200 lines.
- **Code says**: Zero panel/extension files in `/mnt/intergenos/intergen/`. Only `cli.py`, `dbus_daemon.py`, `data/{intergen.service, com.intergenos.InterGen.service}`. Plan's 4-package AI tier shrank to 2 (`intergen` + `llama-cpp`); `intergen-gnome-extension` and `intergen-glasswing` packages don't exist. Design doc `intergen_panel_ui_design_2026-04-09.md` exists but no code matches.
- **Why drifted**: All April 14-17 effort went into routing/prompting/grader iteration. UI was deprioritized in favor of getting the 2B's response quality to PASS-grade.
- **Reviewer impact**: "InterGen lives in the GNOME panel" claim in plan §Context is currently aspirational. CLI works, D-Bus works, but no chat panel ships.
- **Cross-ref**: laptop's A11 reframes this as "Phase 2 disposition: production / paused / stale" — owner-architect-required.

#### DRIFT-7: Glasswing MCP guard is stub-only [M]
- **Plan said**: Glasswing — schema hash pinning, tool-description injection scanning, full audit logging to `/var/log/intergen/mcp-audit.log`, rate limiting, seccomp sandbox.
- **Code says**: `mcp_client.py` exists (`9f6ad38` 2026-04-14) — guard is plumbed in but schema-pinning, audit-log, sandbox enforcement haven't been implemented. No `/etc/intergen/mcp.d/*.yml` permission-manifest handling. No `intergen-glasswing` package.
- **Why drifted**: Same reason as DRIFT-6 — model quality iteration consumed budget.
- **Historical framing clarification (verified 2026-04-27 against 3 April 9 docs)**: "Glasswing" was always the InterGenOS-side architecture name with **Claude API as THE designated provider** (`mcp_security_architecture_2026-04-09.md`: *"Claude API for tool description injection scanning"*; `intergen_semantic_matching_research_2026-04-09.md`: *"Security auditing via Glasswing (Claude API)"*; `intergen_phase2_guidance.md` line 46: `GlasswingGuardInterface`). Today's vendor-neutral pivot generalizes Glasswing-Anthropic to ONE OF SEVERAL scanner providers — that's a clean architectural generalization of the original April 9 intent, not a rename.
- **Reviewer impact**: **HOLY GRAIL-relevant.** Anyone trusting "MCP traffic goes through Glasswing" today is wrong. Must not ship MCP-enabled defaults until audit-log + schema-pin path is real code. Shim-review reviewers may probe this. **Phase 2.5 InterGen Sentinel architecture (umbrella name, owner-canonical 2026-04-27 evening)** closes this gap properly + sharpens the project's HG posture (no single-vendor blast radius for security claims). Sentinel includes 8 providers: 2 local default (`Local-Rules` rule-based + `Local-Qwen` InterGen-LLM-backed — uniquely InterGenOS), and 6 cloud opt-in (`Glasswing-Anthropic`, `Gemini-Google`, `CoPilot-Microsoft`, `ChatGPT-OpenAI`, `Grok-xAI`, `DeepSeek`). README.md public-positioning corrected 2026-04-27 evening to match.

#### DRIFT-9: Cloud escalation — wire-only, zero adapters shipped [M]
- **Plan said**: Pre-built provider adapters for Anthropic, OpenAI, Google, Mistral, DeepSeek, xAI, Custom.
- **Code says**: `llm.py:96` has `self._cloud_providers: dict[str, Any] = {}` — dict scaffold present, no provider modules in `interfaces/cloud.py` beyond the abstract interface. `EscalationMode` enum defined; `_api_call_count` tracks zero calls.
- **Reviewer impact**: "Phone a friend" feature does not work in shipped code. User docs that promise this feature are aspirational.

#### DRIFT-2: Compound decomposition is tier-aware, with per-tier action thresholds [M]
- **Plan said**: Compound queries handled by router (no specifics).
- **Code says**: `decomposer.py:26-30` — Tier 1 splits at >1 action, Tier 2 at >3, Tier 3 at >5. `analyze_query()` takes `HardwareTierLevel`. Commit `f13cccf` 2026-04-14.
- **Reviewer impact**: `ConversationRouter.__init__` now takes `hardware_tier` as required init arg. P0 = compound, not "first cheap match" as plan implied.

#### DRIFT-4: Adaptive prompt composition replaced fixed system prompt [M]
- **Plan said**: Single-block system prompt with hardcoded persona.
- **Code says**: `llm.py:28-73` — `_BASE_PROMPT` (~100 tokens, 3 rules) plus one of 4 `_MODIFIERS` (`identity`/`diagnostic`/`safety`/`general`) selected by classifier. Commits `a85ad60` `d8e0c82` 2026-04-15/16.
- **Why drifted**: R20 finding — irrelevant rules hurt small models. Owner-flagged as 7-rounds-of-testing finding.
- **Reviewer impact**: Anyone reading `_BASE_PROMPT` alone is missing 30-50% of the actual prompt at runtime.

#### DRIFT-5: Tool count is 8, not 7 — `analyze_file` was added [M]
- **Plan said**: "Core Tools (7)" — run_command, read_file, write_file, manage_packages, manage_services, web_search, open_application.
- **Code says**: 8 modules in `intergen/tools/` — the 7 above plus `analyze_file.py` (commit `c76081d` 2026-04-14, "LLM-powered file comprehension").
- **Reviewer impact**: Documentation, tool-count assertions, "what InterGen can do" lists need updating. `analyze_file` is LLM-augmented (sends file contents through model) — different safety profile than `read_file`.

#### DRIFT-10: Test suite + grader are now load-bearing infrastructure — not in plan [M]
- **Plan said**: "50-query semantic matching test suite" (one line in §Verification).
- **Code says**: 71 unit tests at `345e4c8` covering 8 tools + hardware detection. PLUS behavioral conversation suite — 17→31→52→102→112 conversations, 149 assertions, self-grader calibrated 5 times across R10-R27 (`fc3f27e`, `eef2111`, `e86a9ec`, `29e066b`, `887599e`).
- **Reviewer impact**: This is now part of the project's quality contract. Any future tier (e.g., 35B) requires its own grader-config dict.

#### DRIFT-8: Models — 9B replaces 8B at Tier 2 (label-only, NOT actually drift) [M]
- Initial tier discussions referenced 8B; plan correctly settled on 9B; code matches plan. Flagged so reviewer doesn't waste time chasing a non-issue. Round logs (R12-R20) variously reference 2B/8B/9B due to iteration churn.

---

### Axis 2: Forge-SB / installer (8 items) — primary observer: laptop architect-side

**Architect-perspective drift.** Plan was largely silent on these specifics; audit sweep + design decisions filled the gaps.

#### DRIFT-L1: efivars bind-mount into chroot — added in audit sweep [L-supp]
- **Plan said**: Silent on efivars during build. Implicit assumption that EFI variable access wasn't needed in the build chroot.
- **Code says**: `installer/backend/bootloader.py` mounts efivars into chroot (commit `7ae8ac1` 2026-04-20, audit item 3). Required for `efibootmgr --create` to write Boot#### entries during installer's bootloader stage.
- **Reviewer impact**: Anyone reading chroot setup script in build_003 won't see this; it's Forge-specific. Holy Grail-relevant: bind-mount is read-only by default in install path; gets masked at chroot teardown.

#### DRIFT-L2: sbsign --output atomic-rename pattern — added in audit sweep [L-supp]
- **Plan said**: Nothing about sbsign atomicity.
- **Code says**: `installer/backend/bootloader.py` writes signed binaries via `sbsign --output <tmp>` then `os.rename(tmp, target)` (`7ae8ac1` audit item 8). Hardens against partial-write corruption.
- **Reviewer impact**: Future signers (GRUB2 PE/COFF re-sign on update) should follow same pattern.

#### DRIFT-L3: sbsign idempotency via sbverify --list pre-check — added in audit sweep [L-supp]
- **Plan said**: Nothing about re-sign avoidance.
- **Code says**: `bootloader.py` runs `sbverify --list <target>` before signing; if valid signature already chains to our cert, skips re-sign (`7ae8ac1` audit item 9).
- **Reviewer impact**: Behavioral contract — installer is idempotent on signing. `test_class1_chain_verify.py` asserts this.

#### DRIFT-L4: Class 1 kernel stage re-gated to post-install — design call [L-supp]
- **Plan said**: Silent on class-stage gating.
- **Code says**: Commit `0138844` 2026-04-20 re-gated Class 1 kernel-stage to post-install (audit item 4 consensus). Build-time runs unit-mocked tests; kernel-trust-anchor step is asserted only on installed target with user's MOK cert at `/var/lib/intergen/mok/mok.crt`.
- **Why drifted**: User is the trust anchor, not us. Asserting kernel signature with our cert at build time would be wrong — we don't HAVE user's cert at build time.
- **Reviewer impact**: Test-count interpretation differs by host (dev laptop = 14 skips, Forge-installed target = up to 143 pass). Feature, not bug.

#### DRIFT-L5: Class 3 (shim-specific) tests DEFERRED — owner-acknowledged [L-supp]
- **Plan said**: Silent on class taxonomy.
- **Code says**: No Class 3 module in `installer/tests/`.
- **Why deferred**: shim is Fedora/MS-signed, not ours. No asset under our control to assert against. sbverify of shimx64.efi against MS cert is property of shim chain, not our build artifact.
- **Reviewer impact**: Harness skips a class number to keep future option open without claiming current coverage.

#### DRIFT-L6: Day 2 Phase B integration tests with real sbsign + sbverify — not in plan [L-supp]
- **Plan said**: Plan §Verification mentioned the InterGen behavioral suite, not Forge SB harness.
- **Code says**: `da1739b` 2026-04-20 added Day 2 Phase B integration tests (47 at the time; subsumed into 146-test count by 2026-04-22). Class 1 integration suite requires sbsign + test cert chain; skips cleanly when not.
- **Reviewer impact**: Test-count by host (dev laptop 132/146 vs ubuntu2404 136/146) hinges on integration-suite gating.

#### DRIFT-L7: pkm packaged as core-tier system tool — not in plan [L-supp]
- **Plan said**: Plan referenced pkm as the package manager but didn't specify pkm-itself-as-package.
- **Code says**: `ab1e986` 2026-04-20 packaged pkm into `packages/core/pkm/` (audit item 2 closed). Includes default-repo stanza wiring (Component 1 of VPS source-mirror design). Components 2 + 3 deferred-tracked.
- **Reviewer impact**: README's `pkm install-helper chrome` Quick Start claim depends on this. End-to-end verification post-v0.1-ISO is the test target.

#### DRIFT-L8: HOLY GRAIL doctrine added above Prime Directive — codified mid-window [L-supp] [W-D.W1]
- **Plan said**: Plan operated under Prime Directive only.
- **Code says**: `4072a5d` 2026-04-18 added Security-Only Alignment doctrine to top of `.claude/CLAUDE.md`. Canonical at `~/.claude/projects/-mnt-intergenos/memory/holy_grail_security_alignment.md`.
- **Why drifted**: Anthropic Mythos Preview (181 working exploits at superhuman scale).
- **Reviewer impact**: All design decisions post-2026-04-18 should evaluate against both directives.

---

### Axis 3: Fleet orchestration (7 items) — primary observer: windows

#### DRIFT-W1: agent_id naming convention — owner-canonical correction [W]
- **Drift**: fleet-flat-hyphen (`gemini-flash`) → owner-canonical `user-machine-vehicle-llm[_version]` with underscore (`gemini_flash`).
- **Today's correction**: validator regex updated 2026-04-27 ~20:00Z to allow underscore (`^[a-z][a-z0-9_-]{1,63}$`); 3 files patched + service restarted clean. Smoke test 8/8 pass.
- **Pending**: full naming-convention prose in canonical 08_agent_onboarding.md (regex updated; segment-structure description not yet).
- **Status**: regex unblocked; doc-prose lane open (Windows offered to co-author).

#### DRIFT-W2: CODER-vs-COMMUNICATOR distinction missing from canonical doc [W]
- **Drift**: 08_agent_onboarding.md treats all Path 2 agents identically; reality (today's Gemini-Flash deliberation) showed role-expectation matters.
- **Owner reversed initial option-b recommendation** today; canonical doc still doesn't capture distinction.
- **Status**: co-author lane (main + windows). Owner-architect-required for the distinction wording.

#### DRIFT-W3: Windows side has no test harness [W]
- **Drift**: All 146 Forge-SB tests run on laptop or build VM. No Windows-side validation surface.
- **Status**: may be intentional (Windows = development-only, not target) but worth explicit classification rather than silent gap.

#### DRIFT-W4: Multi-connector identity model post-dates 08_agent_onboarding.md initial framing [W]
- **Drift**: Model finalized today; doc updated today to document Path 1 (Claude multi-connector) + Path 2 (non-Claude programmatic). Older single-connector framing may persist in places.
- **Status**: audit pass on canonical doc to scrub legacy framing references.

#### DRIFT-W5: Phase 4d retag not uniformly applied [W]
- **Drift**: Channel agent_id retagged via dedicated connector (functionally complete). VPS submissions/ dir still named `claude-windows/` not `chris-windows-code-claude/`.
- **Status**: cosmetic but fleet-wide consistency issue. Owner-driven dir-rename queued.

#### DRIFT-W6: TEXT-ONLY ratification arrived 2026-04-27 mid-window [W] [L-supp retraction]
- **Drift**: POWER RULE codified today; voice cleanup edits across 5 surfaces. Windows side has no voice-related code so no impact on Windows lane, but flag for canonical record.
- **Effect**: A8 (whisper.cpp + Piper TTS templates) is RETRACTED — not a gap, not deferred, OUT entirely.

#### DRIFT-W7: IDE-per-agent-type POWER RULE arrived 2026-04-27 [W]
- **Drift**: Claude=VS Code (Microsoft); non-Claude=Codium. Codified today after main's terminology error.
- **Status**: mirror to canonical fleet ruleset pending.

---

## OWNER-ARCHITECT-REQUIRED ITEMS

These cannot be unblocked by fleet alone. Each requires your decision before forward motion.

1. **A11 / DRIFT-6: intergen/ Phase 2 disposition** [L primary] — production / paused / stale? Plan-budgeted 4-package AI tier shrank to 2 (no GTK4 panel, no GNOME extension, no `intergen-glasswing` package). Affects whether v1.0 ships with the AI assistant or not. Two of four planned ai-tier packages don't exist. Touches voice scope (now retracted) directly.

2. **C.W2: D1 signing-key custody draft (uncommitted) reconcile** [W primary] — `docs/research/installer/signing_key_custody_2026-04-18_draft.md` (17159 bytes) sits in Windows clone git status as untracked. Committed final `signing_key_custody_2026-04-18.md` (24957 bytes) is 5 days newer. Either purge or content-diff. **Imminent**: Nitrokey window opens 2026-04-28 (now).

3. **D5: Documentation scope outline (v1.0 minimum-viable user docs)** [M + L] — placeholder-only memory entry; sketch v1.0 minimum-viable user doc set (install guide, getting-started, InterGen intro, pkm intro, security posture). Awaits owner priority input. Gemini-Flash's natural lane post-onboarding.

4. **D8: rules-canonical-v1 Phase 5 + Phase 6 arbitrations** [L primary] — branch `rules-canonical-v1` at `48804dd` (Phase 2 diff, 321 lines, 5 arbitration items at §10):
   - Rule #4 numbered-vs-top-tier
   - Rules 5-10 bundled-vs-enumerated
   - Same-concept-different-framing pairs
   - Tests-are-truth-vs-define-behavior
   - Build-feedback scope
   - **(Bonus)** MEMORY.md vs handoff.md divergence on rule #5 binding (NEVER BLOCK FOREGROUND vs IDLE = PRODUCTIVE — both real, only number contested)

5. ~~**Forge GUI framework choice**~~ — **RESOLVED 2026-04-10** per [`live_session_and_installer_FINAL_2026-04-10.md`](/home/christopher/intergenos/research/installer/live_session_and_installer_FINAL_2026-04-10.md). Custom Forge installer chosen over Calamares + 8 alternatives evaluated (Anaconda, Subiquity/Curtin, distinst, Albius, COSMIC, archinstall, d-i, EndeavourOS-customized-Calamares). GTK4 + libadwaita locked. 6-screen flow specified. 833-line backend + 417-line TUI frontend already built. DeepSeek 1,652-line GUI proposal as reference. Hybrid BIOS+UEFI ISO with custom ~100-line LFS-style init + GLASSWING SHA256 integrity check + unsquashfs install (~150s vs ~284s rsync). **Status: implementation queued, awaiting capacity.** NOT owner-architect-required.

6. **DRIFT-W2 / D.W11: CODER vs COMMUNICATOR canonical doc role-expectations section** [W primary, co-author with main] — owner-canonical correction today reversed initial option-b. Doc-side wording still pending.

**Adjacent decisions worth surfacing**:

- **A.W4 / A9: deferred_hardening_2026-04-01 sweep completeness** — were ALL items addressed in 4e16380 (igos-build) + 7ae8ac1 (forge-sb), or partial? Worth verify against original deferred list before declaring closed.
- **D.W10 / A10: README package-count drift** — CLAUDE.md/README.md claim 458; real ~654 across 6 tiers (28+112+20+431+61+2). Doc-fix item; surfaces accurate scope to project visitors.

---

## HARD-DATE CALENDAR

| Date | Item | Gating |
|---|---|---|
| 2026-04-28 → 2026-05-02 | Nitrokey arrival (Rebecca fast-tracked from Teltow) | Shipping |
| 2026-04-28+ | C.W2 reconcile (must complete BEFORE ceremony) | Owner-architect |
| Week of 2026-04-28 | Signing-key ceremony — C2 Tails air-gap root key, C3 subkeys onto hardware | Hardware in-hand |
| 2026-05-02 | Fedora shim-maintainers advisory email send (B5) | Draft ready |
| ~2026-05-04 | Re-raise peer-review target selection (B4) | Time |
| 2026-05-15 | Shim-review PR-open target | A1 ISO + B1 README + B2 Dockerfile converge |
| 2026-06-27 | MS 2011 CA hard deadline (6-week buffer from PR-open) | Hard |

**Five-week cluster**: all B-series, C-series, A1 must clear by 2026-05-15 PR-open.

---

## MERGED INVENTORY — KEY DEFERRED (Top 10 highest-leverage)

Ordered by Holy Grail security alignment + capability multiplier + hard-deadline pressure.

1. **A1: v0.1-shim-review pre-release ISO build** [M] [L] [W] — keystone item. Single biggest unblocker (gates A2 laptop Phase A-2 empirical, B2 Dockerfile testing, A6 pkm helper testing). Multi-hour full-pipeline build via `scripts/build-intergenos.sh`. **Owner: main / build orchestrator. No external gating.**

2. **C.W2: D1 signing-key custody draft reconcile** [W] — IMMINENT (Nitrokey window opens 2026-04-28). Owner-architect-required.

3. **B1: Shim-review README 39-question population** [M] [L] [W] — currently ~50% at private skeleton. Owner-confirmed answers ready to drop in. Drives 2026-05-15 PR-open. **Owner: windows per TRACKER.**

4. **C1: Nitrokey first-touch + onboarding pre-flight checklist** [M] [L] — append firmware/PIV/PIN/PUK/test-cert checklist to `docs/signing-procedure.md`. Day-one becomes "follow checklist" not "improvise." **Owner: main or laptop writes; owner runs.**

5. **A2: Phase A-2 plumbing — 3 mechanical sub-tasks** [L primary] — parser shipped (`b50d252`, 17 tests). Remaining (a) grub.cfg test-harness write path ~1-2h; (b) serial console capture from VM-boot ~2-3h; (c) compose parser into `test_grub_check_signatures` ~1-2h once a+b land. **Gating**: A1 ISO + libvirtd wake.

6. **A4 (laptop) / A2 (main): Zephyrus kernel config audit** [M] [L] — cross-check universal-baseline + x86-64-v2 against Ryzen 9 / RTX 3070 Ti / Fanxiang S880E. Output: `docs/research/kernel/zephyrus_config_audit_2026-04-23.md`. Gates real-hardware first-light.

7. **A5 (laptop) / A3 (main): NVIDIA driver-open vs proprietary feasibility for Zephyrus Ampere (RTX 3070 Ti)** [M] [L] — research + recommendation for first-light install path. Output: `docs/research/kernel/nvidia_driver_open_2026-04-23.md`.

8. **B2: Dockerfile (reproducibility build) skeleton + iteration** [M] [L] [W] — required artifact for shim-review PR. Pinned base image SHA, `SOURCE_DATE_EPOCH`, `LANG=C`/`TZ=UTC`, deterministic tar, pinned shim 16.1, placeholder vendor cert. Multi-host byte-match verification (laptop + ubuntu2404). **Vendor cert plug-in is post-ceremony.**

9. **B5: Fedora advisory email send-prep** [M] [L] [W] — draft READY at `~/intergenos/research/shim_review/advisory_email_draft.md`. Owner sends from his client 2026-05-02 (T-5 days from compile).

10. **A6 (laptop) / A4 (main): Install rehearsal walkthrough** [M] [L] — step-by-step Forge install on Zephyrus from current installer code + Fanxiang notes. Surfaces gotchas pre-hardware. Output: `docs/research/installer/zephyrus_install_rehearsal.md`. **Laptop has unique perspective as bootloader/MOK plumbing author** — co-author or review candidate.

---

## A. DISTRO BUILD WORK

### A1. v0.1-shim-review pre-release ISO build [M] [L] [W]
- **Status**: queued; ALL gating dependencies clear
- **Owner**: main / build orchestrator
- **Gating**: none — multi-hour full-pipeline build
- **Downstream unblocks**: A2 Phase A-2 plumbing, B2 Dockerfile testing, A7 pkm helper testing, Forge SB bare-metal first-light

### A2. Phase A-2 plumbing — 3 sub-tasks (a/b/c) [L primary]
- **Status**: in-flight; parser layer SHIPPED (`b50d252`, 17 tests, mine)
- **Owner**: laptop
- **Gating**: A1 ISO + libvirtd wake on ubuntu2404

### A3. Forge SB test harness — 146 tests, feature-complete [L primary, W co-author]
- **Status**: SHIPPED feature-complete
- **Counts**: Class 1 (27 unit + 4 integration), Class 2 (19), Class 2b (23), Class 5 (26), Phase A-1 (4), Phase A-2 parser (17), post-install integration (6) = 126 + integration variants
- **Class 3**: DEFERRED (DRIFT-L5)
- **Expected by host**: dev laptop 132/146 pass + 14 skip; ubuntu2404 136/146 + 10 skip; Forge-installed target up to 143 + 3 skip

### A4. Zephyrus kernel config audit [M] [L]
- **Status**: queued
- **Owner**: laptop preferred per TRACKER (test-harness + hardware-integration); could be main
- **Output**: `docs/research/kernel/zephyrus_config_audit_2026-04-23.md`

### A5. NVIDIA driver-open feasibility for Zephyrus Ampere [M] [L]
- **Status**: queued
- **Owner**: main; could be windows since they're at Zephyrus
- **Output**: `docs/research/kernel/nvidia_driver_open_2026-04-23.md`

### A6. Install rehearsal walkthrough (Zephyrus / Forge) [M] [L]
- **Status**: queued
- **Owner**: main; laptop co-author candidate
- **Output**: `docs/research/installer/zephyrus_install_rehearsal.md`

### A7. pkm fetch-package / proprietary-helper end-to-end test [M] [L]
- **Status**: noted; pkm itself packaged as core-tier (`ab1e986`, audit item 2 closed). Helpers at `packages/extra/{chrome,vscode,claude-code,brave,discord,edge,spotify,thunderbird}-helper`
- **Owner**: main or laptop post-image
- **Gating**: A1 ISO

### A8. **RETRACTED** — voice tier (whisper.cpp / Piper TTS / espeak-ng)
- **Per text-only POWER RULE** (`feedback_intergen_text_only.md`): voice descoped per hardware-tier latency research at architecture plan. Shipped scope `packages/ai/intergen + packages/ai/llama-cpp` is COMPLETE.
- **Doc-update lane** (small follow-up): clean voice references in README.md + CLAUDE.md tier descriptions where stale-plan language persists. Folds into A10 doc-drift sweep.

### A9. Build orchestrator deferred-hardening backlog sweep [M] [L]
- **Status**: queued
- **Open question (A.W4 [W])**: Were all items in `~/intergenos/research/build_system/deferred_hardening_2026-04-01.md` addressed in `4e16380` (igos-build) + `7ae8ac1` (forge-sb), or partial sweep? Verify against original list, close-or-reprioritize.
- **Owner**: laptop or main

### A10. CLAUDE.md / README.md drift fix sweep [L] [W]
- **Status**: NEW
- **Includes**: package count (claimed 458, real ~654 across 6 tiers: 28 toolchain + 112 core + 20 base + 431 desktop + 61 extra + 2 ai); tier descriptions naming voice (stale-plan reference per A8 retraction); other doc-versus-reality drifts surfaced during synthesis
- **Owner**: any agent on next idle

### A11. intergen/ Phase 2 disposition [L primary] [DRIFT-6 cross-ref]
- **Owner-architect-required.** See OWNER-ARCHITECT-REQUIRED #1.

### A12. GNOME 49 / desktop tier (431 packages) [M-A11] [L-A12]
- **Status**: templates exist; presumed stable since 2026-04-06 clean-build remediation. No reported in-flight desktop-build issues over 7-day window.
- **Owner**: main on next desktop-build pass

### A13. 35B AI tier scoping session [M-A8]
- **Status**: parked, "needs owner bandwidth for session design"
- **Owner**: owner-only

### A14. AI tier added to build system (gap-window shipped) [M-drift A1]
- **What**: `"ai"` added to `VALID_TIERS` in `igos-build/parser.py`; tier_priority position 6. Commit `1f139fe` 2026-04-09.
- **Status**: completed-needs-followup — `intergen-glasswing` and `intergen-gnome-extension` packages still missing → DRIFT-6/7.

### A15. llama-cpp package template (gap-window shipped) [M-drift A2]
- **What**: `packages/ai/llama-cpp/` — pinned `b5545`, `LLAMA_BUILD_SERVER=ON`, `LLAMA_CURL=ON`, Vulkan auto-detect.
- **Status**: completed; still single-variant (no x86-64-v2/v3 split per system_eval issue #8).

### A16. intergen package template + man page (gap-window shipped) [M-drift A3]
- **What**: `packages/ai/intergen/{package.yml, build.sh, intergen.1}`. Man page added in shim-runway batch (`8bb9849`).
- **Status**: completed for build infra; runtime functionality see DRIFT-6/9.

### A17. 48 application templates added (gap-window shipped) [M-drift A4]
- **Commit**: `d834208` 2026-04-09. Bulk add of must-have apps + dependency chains.
- **Status**: completed. system_eval_2026-04-10 flagged 15 missing apps; reduced today (jq newly flagged via E.W6).

### A18. Phase 3 boot animation DRM/KMS (gap-window shipped) [M-drift A5]
- **Commit**: `513cf3d` 2026-04-09.
- **Status**: completed; awaits FLUX-generated assets.

### A19. **x86-64-v2 full rebuild has NOT been executed** [M-drift A8]
- **Status**: CFLAGS plumbing committed (`5d39331`, `c72d74f` 2026-04-10), but actual full rebuild against x86-64-v2 not run. **Current image is x86-64-v3.**
- **Reviewer impact**: Public-release ISO blocked. Affects what binary the shim-review PR points at. Lenovo i5-3570 (Ivy Bridge) was the original failure case — without v2 rebuild, target excludes Sandy/Ivy/early-Westmere CPUs.
- **Owner**: main / build orchestrator

### A20. Visual branding lockdown — completed gap-window [M-drift A9] [L-supp A-L10]
- **Commits**: `eef03b2`, `79e32c9`, `a06c139`, `c0f74a8` 2026-04-11. Heartbeat-pulse logo, bold-edition theme, README screenshots, `VISUAL_LANGUAGE.md`.
- **Status**: completed.

### A21. InterGen Phase 2 foundation + 9 core modules + tools + daemon (gap-window shipped) [M-drift A10-A19]
- **Commits**: `c77f8a3`, `a4909b0`, `3439bae`, `b363226`, `b35b7eb`, `06b9f35`, `8984bc1`, `ab67410`, `fe0f446`, `e3018ca`, `55f0353`, `3e679af`, `7e88311`, `a85ad60`, `8d3758e`, `1710001`, `5949b5d`, `1c070eb`, `02dc620`, `81ae455`, `ec887a9` (2026-04-14 → 2026-04-16).
- **Status**: completed for routing + state cache + adaptive prompting + tier wiring + behavioral test harness. Cloud side stubbed (DRIFT-9). UI not built (DRIFT-6). Glasswing wire-only (DRIFT-7).

### A22. Round 18-28 model iteration (gap-window shipped) [M-drift A16]
- **Status**: R28 (9B retest) = 105 PASS / 7 MIXED / 0 FAIL. Three remaining issue families documented in `code_review_packet/B5_known_remaining_issues.md`.
- **At-rest since**: 2026-04-17. Further iteration deferred until reviewer feedback or scope-change.

### A23. Forge SB installer hardening — backend code (gap-window shipped) [L-supp A-L2]
- **Commits**: `bbf380a`, `77043a5`, `9716545` 2026-04-18; `c70642a` 2026-04-19; `6c5158c`, `7ae8ac1` 2026-04-20.
- **Status**: feature-complete for ship; bare-metal first-light deferred to post-ceremony / post-Zephyrus-install.

### A24. Forge SB test harness — Day 1 → Day 2 Phase B → audit-sweep (gap-window shipped) [L-supp A-L1]
- **Commits**: `5e861e9`, `da1739b`, `f2ce375`, `0138844`, `8ea3ee8`, `15cf27a`, `ece0e84`, `288b6db`, `b50d252` (2026-04-19 → 2026-04-21).
- **Status**: feature-complete at 146 tests; Phase A-2 empirical gates on A1 + libvirtd.

### A25. igos-build audit-sweep hardening (gap-window shipped) [L-supp A-L3] [W-A.W4]
- **Commit**: `4e16380` 2026-04-20.
- **What**: SHA256 case-insensitive comparison, URL parameter validation, `shlex.quote` on subprocess invocations, atomic deploy via temp-then-rename, dependency-cycle reporting in graph.py.
- **Open**: A9 sweep completeness verification.

### A26. Kernel + iptables nftables-only Track 2 (gap-window shipped) [L-supp A-L4] [W-A.W7]
- **Commit**: `74054e4` 2026-04-18. Closes system_eval issue #1 (libnftnl/nftables not built).
- **Reviewer impact**: Distro firewalls expect nftables syntax; iptables binaries via `iptables-nft` shim only. User docs showing iptables examples need updating.

### A27. Steam helper deferred (gap-window) [M-drift A6]
- **Commit**: `298b3f7` 2026-04-09. steam-helper removed pending multilib support.
- **Status**: correctly deferred.

### A28. Intel microcode + pkm in image (gap-window shipped) [M-drift A7]
- **Commit**: `67bc6dd` 2026-04-09. pkm formal system-tool packaging happened later (`ab1e986` 2026-04-20).

### A29. desktop tier dependency expansion (gap-window shipped) [L-supp A-L7] [W-A.W6]
- **Commit**: `827009e` 2026-04-18. gnome-text-editor dep chain (libunwind, sysprof, editorconfig).

---

## B. SHIM / SECURE BOOT WORK

### B1. Shim-review README 39-question population [M] [L] [W]
- **Status**: ~50% at `~/intergenos/research/shim_review/README_draft_skeleton.md` (private)
- **Owner**: windows per TRACKER
- **Owner-confirmed answers ready**: legal entity = Christopher Cork; release URL = GitHub Releases pre-release (depends on A1); Ethan email = `ethan@intergenstudios.com`; community cross-sign = merit-first; peer-review = May 4 selection
- **Hard date**: 2026-05-15 PR-open

### B2. Dockerfile reproducibility — skeleton + iteration [M] [L] [W]
- **Status**: queued
- **Spec**: pinned base SHA, `SOURCE_DATE_EPOCH`, `LANG=C`/`TZ=UTC`, deterministic tar, pinned shim 16.1, placeholder vendor cert
- **Owner**: main authors; multi-host byte-match (laptop + ubuntu2404) is verification
- **Gating**: vendor cert post-ceremony; skeleton itself unblocked
- **Hard date**: 2026-05-15

### B3. SBAT generation reconciliation [M] [L]
- **Status**: queued; validate "generation 1 for new vendor entry" reasoning
- **Owner**: windows
- **Gating**: Fedora advisory thread (B5)

### B4. Peer reviews (shim-review PRs) [M] [L]
- **Status**: queued; 2-3 open PRs at `github.com/rhboot/shim-review/pulls`, Chris + Ethan split
- **Owner**: owner + Ethan
- **Gating**: Ethan onboarding (D2), 2026-05-04 selection date

### B5. Fedora advisory email send (target 2026-05-02) [M] [L] [W]
- **Status**: draft READY at `~/intergenos/research/shim_review/advisory_email_draft.md`
- **Owner**: owner sends from his client
- **Gating**: 2026-05-02 calendar (T-5 days)

### B6. Hard-date wall — MS 2011 CA expiry buffer [M] [L]
- **2026-06-27** = 6-week buffer before MS 2011 CA hard deadline
- All B-series must clear well before this

### B7. Forge SB harness — Phase A-2 empirical confirmation [L primary]
- **Status**: parser shipped; empirical gated on A1 + libvirtd wake
- **Hypothesis status**: ~90% confirmed at mechanism level. Empirical run nails the 10%.
- **Owner**: laptop

### B8. Day 1 Class 1 + 27 unit tests (gap-window shipped) [L-supp B-L1] [W-B.W2]
- **Commit**: `5e861e9` 2026-04-19. All passing in 14ms.
- **Status**: completed; expanded across 7-day window.

### B9. shim vendor cert landed on source mirror (gap-window) [M-drift B6]
- **Date**: 2026-04-19 per context_transfer. WC uploaded shim RPM to source mirror; sha256 round-trip verified.

### B10. shim-review README + Dockerfile + Fedora advisory + MS 2011 — same-line items
- See B1 / B2 / B5 / B6 above. Listed for completeness; the dependencies converge at A1 ISO build.

### B11. installer/backend/mok.py architectural notes [L-supp B-L2]
- MOK enrollment flow hooks into `MokManager.efi` at first-boot via `mokutil --import`. Test cert path is placeholder; real vendor cert from PIV slot 9c plugs in here post-ceremony (covered by C6).

### B12. Forge SB Class taxonomy intent [L-supp B-L1]
- Numbering skips 3 (deferred per DRIFT-L5) and 4 (was kernel-cmdline class, merged into Class 2 runtime probe). Intentional, reflects current scope honestly.

---

## C. SIGNING-KEY CEREMONY

### C1. Nitrokey first-touch + onboarding pre-flight checklist [M] [L]
- **Status**: queued; deliverable is appendix to `docs/signing-procedure.md`
- **Owner**: main or laptop writes; owner runs
- **Gating**: Nitrokey shipment (ETA 2026-04-28..05-02 = now)

### C2. Tails air-gap root key generation [M] [L]
- **Status**: queued for ceremony week
- **Owner**: owner-only (air-gapped)
- **Spec**: RSA 4096 or Ed25519, no expiry on primary, 2-year on subkeys, UID `Christopher Cork <chris@intergenstudios.com>`. Paperkey + 2× LUKS USB backup (home safe + bank SDB). Export revocation cert.

### C3. Subkeys onto hardware [M] [L]
- **Status**: queued for ceremony week
- **Owner**: owner-only
- **Spec**: [S] release-signing subkey via `gpg --edit-key`. PIV slot 9c gets EFI X.509 keypair. Touch required on both.

### C4. Cross-sign with Ethan [M] [L]
- **Status**: awaits Ethan's Phase 1 (his PGP keypair on his own hardware)
- **Owner**: owner + Ethan
- **Gating**: Ethan onboarding (D2)

### C5. Publish pubkey + cross-publish fingerprint [M] [L]
- **Status**: queued post-ceremony
- **Owner**: owner + main
- **Spec**: keys.openpgp.org upload; cross-publish `docs/signing-key.md` + GitHub releases + intergenstudios.com signing-key page (post-v1)

### C6. Shim vendor cert export + Forge MOK enrollment registration [M] [L]
- **Status**: queued post-ceremony
- **Owner**: main / installer lane
- **Co-review**: laptop on `installer/backend/mok.py` side
- **Gating**: C3 complete; B2 Dockerfile vendor-cert plug-in depends on this

### C7. sign-release.sh skeleton verification [M] [L]
- **Status**: SHIPPED skeleton (`d1ac4cc`) — fails fast if hardware token absent
- **Owner**: main
- **Gating**: C2-C3 complete

### C8. D1 signing-key custody draft (UNCOMMITTED) reconcile [W primary]
- **Owner-architect-required.** See OWNER-ARCHITECT-REQUIRED #2.

### C9. D1 signing-key custody — committed final (gap-window shipped) [W-C.W1]
- **Commit**: `3ced5f5` 2026-04-19. File `docs/research/installer/signing_key_custody_2026-04-18.md` (24957 bytes).

### C10. MS shim sponsorship research (gap-window shipped) [W-C.W3]
- **Commit**: `3ced5f5` 2026-04-19. File `docs/research/installer/ms_shim_sponsorship_2026-04-18.md` (18170 bytes).
- **Status**: research-only; informs whether we self-sign or piggy-back on a sponsoring distro.

---

## D. DOCS / POLICY / GOVERNANCE

### D1. Succession scaffold (Ethan packet response pending) [M] [L]
- **Status**: scaffold v2 SHIPPED to private location; public policy-level `docs/governance/succession.md` (`ba3b227`); packet sent 2026-04-21
- **Owner**: owner-driven inbound
- **Gating**: Ethan response (SSH pubkey, GitHub username, agent_id list, OOB secrets-delivery address)

### D2. Ethan provisioning execution (post-response) [M] [L]
- **Status**: queued
- **Owner**: owner + windows (Ethan onboarding lane)
- **Gating**: D1 inbound

### D3. Senior advisor row reinstatement [M] [L]
- **Status**: ROW REMOVED (`a4b8f10`) pending bandwidth confirmation from candidate
- **Owner**: owner-driven outbound

### D4. Retro man pages decision memo [M] [L]
- **Status**: queued. pkm(1) + intergen(1) shipped. Forge / igos-build / blfs-query: separate decision needed.
- **Owner**: windows

### D5. Documentation scope outline (v1.0 minimum-viable user docs) [M] [L]
- **Owner-architect-required.** See OWNER-ARCHITECT-REQUIRED #3.

### D6. Hall of Fame / Security Hall of Fame page [M] [L]
- **Status**: post-v1.0 deferred. Rebecca (Nitrokey fast-track) deserves a mention.

### D7. Signing-key web page (intergenstudios.com/signing-key) [M] [L]
- **Status**: post-v1.0 deferred

### D8. rules-canonical-v1 Phase 5 + Phase 6 [L primary]
- **Owner-architect-required.** See OWNER-ARCHITECT-REQUIRED #4.

### D9. CLAUDE.md HOLY GRAIL doctrine added (gap-window shipped) [L-supp D-L1] [W-D.W1]
- **Commit**: `4072a5d` 2026-04-18. Security-Only Alignment doctrine at top of `.claude/CLAUDE.md`.
- **Status**: completed.

### D10. SECURITY.md disclosure policy + dual-boot Zephyrus playbook v2 (gap-window shipped) [L-supp D-L2] [W-D.W7]
- **Commit**: `92781c3` 2026-04-19. Later tidied in `60f1add` (W-D.W8) and `de4f287` (W-D.W9).

### D11. CODER vs COMMUNICATOR canonical doc role-expectations section [W primary, co-author with main]
- **Owner-architect-required.** See OWNER-ARCHITECT-REQUIRED #6.

### D12. agent_id naming convention regex unblocked + prose pending [W primary]
- **Status**: regex updated 2026-04-27 ~20:00Z (server.py:373, mint-dcr-token.py:35, canonical 08_agent_onboarding.md:22). Service restarted clean. Smoke test 8/8 pass.
- **Pending**: full naming-convention prose section in canonical doc (segment-structure description, hyphen-vs-underscore semantics).
- **Owner**: windows authors prose; main reviews / merges.

### D13. POWER RULE codifications today [W-D.W2/D.W3]
- **TEXT-ONLY** (`feedback_intergen_text_only.md`)
- **IDE-per-agent-type** (`feedback_ide_per_agent_type.md`)
- **Frontier models are peers** (`feedback_frontier_models_are_peers.md`)
- **Curl-fallback POSTs require manual READ-before-POST discipline** (`feedback_curl_post_manual_discipline.md`)
- **Status**: SHIPPED today as memory entries; mirror to canonical fleet ruleset pending.

### D14. Cross-review wording landings [W-D.W6]
- HG-scope-clause + owner-canonical "WE CANNOT AFFORD TO GUESS OR ASSUME" surfaced from windows during Phase 4 reviews; captured in canonical rules but not enumerated as windows-side deliverable elsewhere.

### D15. README refresh (gap-window shipped) [W-D.W10]
- **Commit**: `5da7cdf` 2026-04-20. Security-Only Alignment, Forge SB, Upcoming, current counts.
- **Sub-issue**: per A10 — package count drift (claimed 458; real ~654). Re-audit needed.

### D16. gitignore allow docs/research/ subtrees (gap-window shipped) [L-supp A-L9] [W-F.W6]
- **Commit**: `c01bbfb` 2026-04-18. Bare `research/` pattern was blocking; now allowed.

### D17. Ethan onboarding scaffold + D1 PGP contact update (gap-window shipped) [W-D.W5]
- **Commit**: `1b5577f` 2026-04-21. 7 docs in `docs/succession/ethan_onboarding/`. Scaffold only — actual encrypted ZIP packet not visible in git or VPS submissions.
- **Note**: ZIP delivered OOB to Ethan 2026-04-21 (per handoff); not stored in git/VPS by design.

---

## E. INFRASTRUCTURE

### E1. VPS source-mirror Components 2 + 3 [M] [L]
- **Status**: DEFERRED-TRACKED. Component 1 (default repo stanza) shipped with pkm packaging (`ab1e986`).
- **Owner**: post-v1
- **Gating**: v1 ship; bumped if urgency changes

### E2. Connector hygiene (web-claude → code-claude rename) [M]
- **Status**: queued (per handoff Monday-queue); main's connector misnamed `chris-ubuntu-web-claude` from URL-scoping accident; re-add at `/mcp/chris-ubuntu-code-claude/` then OAuth consent. Orphan OAuth client cleanup batches at session-end.
- **Owner**: owner-driven

### E3. Gmail filter setup [M] [L]
- **Status**: parked. 3 filters needed (Nitrokey inbound, security@, GitHub intergenos advisories).
- **Owner**: owner-only

### E4. Dual-boot Zephyrus playbook [M] [L]
- **Status**: parked post-first-light

### E5. Switchable desktops post-v1 [M] [L]
- **Status**: parked. Per-tier architecture supports the split.

### E6. libvirtd wake on ubuntu2404 [L primary]
- **Status**: sleeping by design (per owner 2026-04-21, not drift). Phase A-2 empirical run gated on this.
- **Owner**: owner or main host operation
- **Gating**: v0.1 ISO arrival = same window as wake
- **Reference**: `feedback_libvirtd_socket_activation.md` memory.

### E7. jq packaging gap [W primary]
- **Status**: NEW gap surfaced today via laptop's hook deploy (no jq on bare InterGenOS install; laptop built 1.8.1 from source after 1.7.1 failed under GCC 15 due to bundled oniguruma incompat).
- **Owner**: main on next package-template authoring pass
- **Spec**: target 1.8.1 (1.7.1 won't build under GCC 15); `--with-oniguruma=builtin --prefix=/usr --disable-docs` based on laptop's empirical build
- **Reference material**: laptop's `~/builds/jq-1.8.1/` retained for template authoring

### E8. Read-before-post hook deployment [M] [L] [W]
- **Status**: SHIPPED on main + laptop (post-VS-Code-restart, production-validated 19:18Z+); Windows queued for rollout.
- **Hook validated in production today**: blocked first POST attempt (MCP reads aged out); cleared retry with fresh tail_since.
- **Owner**: main authored; windows for rollout

### E9. agent_id validator regex update [W flagged, M executed]
- **Status**: SHIPPED 2026-04-27 ~20:00Z. See D12.

### E10. intergen-mcp fingerprint patch (today) [W-E.W5]
- **Status**: SHIPPED 2026-04-27 ~02:27Z (`BearerAuthMiddleware` `fingerprint_matches` loosen-on-empty-current-fp). Backup at `/opt/intergen-mcp/server.py.bak.20260427T022717Z`. Enabled laptop's clean re-bind to `chris-intergenos-code-claude`.

### E11. VPS canonical rules consolidation (gap-window→today) [W-E.W1]
- **Path**: `~/agent-rules/{current,canonical,_archive,submissions,methodology}/`
- **Status**: v3 (9 grouped category docs in current/) + canonical/08_agent_onboarding.md + bind-mounted into intergen-mcp service. End-state MCP resource `intergen://rules/canonical` (Phase 8 wiring deferred until rules stabilize).

### E12. MCP wrapper VPS deployment (gap-window shipped) [M-drift E1]
- **Date**: 2026-04-19. Live `intergen-mcp.service` on VPS, 5 tools, Bearer auth, Streamable HTTP, Python 3.11 system, Apache reverse proxy via cPanel userdata include.

### E13. VPS access parity + sudo passphrase distribution (gap-window) [M-drift E2]
- **Date**: 2026-04-19. WC's ed25519 key on `christopher@origin:2200`; `c-vps-pe.txt` distributed to all 3 agent machines.

### E14. Shim source mirror access (gap-window) [M-drift E3]
- **Date**: 2026-04-19. WC's write access to source mirror; shim vendor RPM upload + sha256 round-trip verified.

### E15. VPS sessions endpoint as drift-detection source [W-E.W7]
- **Observation**: VPS sessions.php goes back to 2026-04-01; rich source for drift detection (April 9 plan verbatim, April 14 Phase-2-in-4-hours moment). Underutilized as analysis surface until today.
- **Worth**: canonical mention as drift-evaluation source.

### E16. Multi-connector identity model (today) [W-E.W3]
- **Status**: SHIPPED 2026-04-27 12:48Z (main on chris-ubuntu-code-claude); laptop validated 13:50Z; windows validated 17:21Z.
- **Submissions/ dir-rename to chris-windows-code-claude/**: still pending (cosmetic, DRIFT-W5).

### E17. Phase 4d retag uniformity [W-DRIFT-W5]
- **Status**: channel agent_id retagged via dedicated connector (functionally complete). VPS submissions/ dir still named `claude-windows/`. Cosmetic; fleet-wide consistency.

---

## F. RESEARCH

### F1. Repo-synced research mirror — re-promotion sweep [M] [L]
- **Status**: 848 files mirrored in `d668649` (2026-04-20). Subsequent home-drive activity (Cline migration, fleet orchestration prior-art, etc.) NOT yet promoted.
- **Owner**: main
- **Gating**: none; quarterly or on-demand

### F2. Aravisian (icons) inbound [M] [L]
- **Status**: awaiting external. US-based, online later 2026-04-22 per Wednesday context transfer; broker-intro via Seth's referral.
- **Owner**: owner-driven
- **Reference assets**: Seth's "Black Glass Mockup" SVG at `~/intergenos/research/branding/icons/design_packet/Black Glass Mockup.tar.xz`

### F3. SethStorm666 future-scope work [M] [L]
- **Status**: door open for smaller-scope future work; NOT pending

### F4. Cline migration retrospective + plan archive [M] [L]
- **Status**: SHIPPED 2026-04-27 by laptop (sha256 `fba07ab...`). Plan-final-v3 archived; retrospective at `/home/christopher/intergenos/research/cline_migration/retrospective_2026-04-27.md`. Throw-vs-return resolved RESOLVED per DeepSeek MONDAYVAL finding.

### F5. PROMPT||GTFO exposure [M] [L]
- **Status**: parked 2026-04-21 out of respect for Ryan's burnout window. Reframed as project-exposure channel.

### F6. Build_003 mining [M] [L]
- **Status**: ongoing background reference; no specific outstanding deliverable

### F7. Hardware test research (gap-window) [M-drift F2]
- **Date**: 2026-04-10. `system_eval_2026-04-10.md` (285 lines, 8 dimensions) + per-system probes for HP 15-dw0xxx, HP 17-ak0xx, Lenovo ThinkCentre 3306g3u.
- **Status**: completed; system_eval is the canonical "where we were on Apr 10" doc. Issue #6 (laptop brightness Fn keys) and Issue #7 (x86-64-v2 rebuild) still open and load-bearing.

### F8. AI-integration research docs 2026-04-14 batch (gap-window) [M-drift F3]
- 6 topical docs at `~/intergenos/research/ai_integration/`: latency, compound queries, messy input, qwen35 thinking mode, testing methodology, competitive landscape. Local-only (not promoted).

### F9. Round 18-28 raw artifacts (gap-window) [M-drift F4]
- 11 rounds × 3 artifacts each ≈ 33 files of test data. Local-only. Aggregate ~25-30 MB. Reviewer materials (B-series + 4 PDFs) are curated subset.

### F10. Code review packet (gap-window shipped) [M-drift A18]
- `~/intergenos/research/ai_integration/code_review_packet/` — A1-A4 architecture + B1-B5 methodology + 4 reviewer PDFs (ChatGPT, DeepSeek, Gemini, Grok) + INTERGEN_CODE_REVIEW_PACKET.md (3210 lines).
- **Status**: completed; reviews returned. B5 lists 3 remaining issue families "one commit away" each (held back per Rule #10).

### F11. R10 cross-comparison + baseline_results (gap-window) [M-drift F5]
- `cross_comparison.md` (3608 lines, 112 queries × 3 rounds = 336 responses), 9 baseline JSONs.

### F12. Removed emelia_paint (gap-window) [W-F.W4]
- **Commit**: `5435793` 2026-04-20. Family project, not research; cleanup.

---

## CLOSED / RETRACTED / SUPERSEDED

- **A8 voice tier (whisper.cpp / Piper TTS / espeak-ng)**: RETRACTED per text-only POWER RULE. Voice OUT entirely (not deferred, not pending). Doc-update tail under A10.
- **Cline migration arc**: CLOSED 2026-04-27T02:21Z. Plan archive + retrospective shipped today (laptop). `tool.execute.before` confirmed working unconditionally per source-of-truth read at `@kilocode/plugin/dist/index.d.ts:231-237`.
- **DRIFT-8 (8B vs 9B)**: NOT actually drift. Plan correctly settled on 9B; code matches plan. Round-log churn referenced 2B/8B/9B inconsistently during iteration.
- **`continue_after_tool_call` agentic-loop fix**: SHIPPED at [`intergen/llm.py:382`](file:///mnt/intergenos/intergen/llm.py#L382), called from [`intergen/router.py:454`](file:///mnt/intergenos/intergen/router.py#L454). Includes timeout/fail handling + empty-synthesis warning. This was queued as P1 in [`phase3_recommendations.md`](/home/christopher/intergenos/research/ai_integration/phase3_recommendations.md) Section 5.3 (joint claude-main + claude-laptop recommendation, 2026-04-15) — *"the most impactful architectural change"* per that doc. Owner had repeatedly observed *"our testing may not be accurately reflecting what InterGen does NEXT"* — that loop is closed. **DRIFT pass didn't surface this** (the pass focused on what was missing, not on additions beyond the original plan).

---

## APPENDIX

### Source files (for audit / further reading)

| File | Path | Format |
|---|---|---|
| Main 7-day primary | `/home/christopher/intergenos/development-status/inventory_main_2026-04-27.md` | Markdown |
| Main drift-pass | `/home/christopher/intergenos/development-status/drift_2026-04-09_to_2026-04-22.md` | Markdown |
| Laptop primary | VPS `~/agent-rules/submissions/claude-laptop/development_inventory_2026-04-27.md` (sha256 `62c8e75633bd4fd0a2e70cf9ba3c215b003ff3da53a451f192051ca264c5fb04`) | Markdown |
| Laptop supplemental | VPS `~/agent-rules/submissions/claude-laptop/development_inventory_gap_2026-04-09_to_2026-04-22.md` (sha256 `2c9771ee0194152e643ee0e30bb98f2239cf02cc34be43959bc6b2c448962193`) | Markdown |
| Windows 18-day | VPS `~/agent-rules/submissions/claude-windows/development_inventory_18day_2026-04-27.md` (sha256 `d08ff4ca9a851eb37696314b74b41be167877047887f1d9c19b21a458edb91a8`) | Markdown |
| VPS sessions endpoint | `https://intergenstudios.com/intergenos/sessions.php?key=...&last=100` | JSON |

### Attribution map (per-agent unique contributions)

**Main lane (chris-ubuntu-code-claude)** — primary author/observer for:
- intergen/-focused DRIFT 1-10 (research subagent)
- Build orchestrator + igos-build (with laptop on `4e16380`)
- VPS infrastructure + MCP server + canonical rules promotion
- Validator regex update execution today
- This synthesis doc

**Laptop lane (chris-intergenos-code-claude)** — primary author for:
- Forge SB test harness 146 tests + Phase A-1/A-2 (`5e861e9` → `b50d252`)
- Forge SB installer backend (`bbf380a` → `7ae8ac1`)
- DRIFT-L1 through L8 (architect-side)
- HOLY GRAIL doctrine codification (`4072a5d`)
- Plan archive + Cline migration retrospective (today)
- Hook install on InterGenOS host today (with jq-from-source build)

**Windows lane (chris-windows-code-claude)** — primary author for:
- Phase A test-harness scaffold + parser co-author (`b50d252`, `f2ce375`)
- Forge-SB hardening review (5th item of 6c5158c via WC review flag)
- Ethan succession scaffold v1+v2 (`1b5577f`)
- DRIFT-W1 through W7
- C.W2 signing-key custody draft observation (Windows-clone-only)
- D.W6 cross-review wording landings (HG-scope, "WE CANNOT AFFORD TO GUESS")

### Drift-origin marker

The transition from "plan in-flight" to "plan as historical reference" happened at **2026-04-14 21:33Z** (`20260414T213346Z-unknown` on VPS sessions). Phase 2 was declared complete in 4 hours vs 8-day budget. The 5 days following went to grader iteration (R10-R20). The 8 days that should have gone to GTK4 panel + GNOME extension + full Glasswing + cloud adapters never materialized.

### Items that survived from system_eval_2026-04-10 (still open)

- **Issue #6** — laptop brightness Fn keys (no recent activity)
- **Issue #7** — x86-64-v2 full rebuild not run (A19 — load-bearing for public release ISO; blocks shim-review PR target binary)
- (Issues #1-5 closed: nftables build inclusion, locale/timezone/rustc PATH/mpv NoDisplay all committed in `5d39331` `c72d74f`)

### Memory entries to mirror to canonical fleet ruleset

These POWER RULES were codified in agent-side memory today; canonical mirror pending:

- TEXT-ONLY (`feedback_intergen_text_only.md`)
- IDE-per-agent-type (`feedback_ide_per_agent_type.md`)
- Frontier models are peers (`feedback_frontier_models_are_peers.md`)
- Curl-fallback POSTs require manual READ-before-POST discipline (`feedback_curl_post_manual_discipline.md`)

### Hard-date pressures within next 5 weeks (recap)

- **2026-04-28..05-02**: Nitrokey arrival; C1-C7 ceremony cluster opens; C.W2 reconcile must complete first
- **2026-05-02**: Fedora advisory (B5)
- **2026-05-04**: Peer-review target selection (B4)
- **2026-05-15**: Shim-review PR-open (A1 + B1 + B2 converge)
- **2026-06-27**: MS 2011 CA hard deadline

---

**End of source-of-truth inventory.** Total unique items merged: ~110 (post-dedup from ~200 raw across 5 sources). DRIFT findings: 25 across 3 orthogonal axes. Owner-architect decisions blocking forward motion: 6.

/chris-ubuntu-code-claude (SPOC), 2026-04-27 ~20:30Z
