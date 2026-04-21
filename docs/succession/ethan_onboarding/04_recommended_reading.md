# Recommended Reading — Why Each One Matters to Us

**Written by:** claude-windows, 2026-04-20 (v2 2026-04-21 — renamed from "required" + reframed as recommendations with rationale)

This is a curated list of what's been load-bearing for how we operate. Not required — recommended, with the why-it-matters-to-us attached so you can decide what's worth your attention.

Reading order below is suggested for people who haven't seen any of it. If you want to jump around based on what's interesting or relevant to your current question, that works — each entry is standalone.

Rough time estimate if read straight through: 2-3 hours. Actual pace is yours.

---

## Tier 1 — Philosophy + Posture (where we'd start)

### 1. `/.claude/CLAUDE.md` — project intro + guiding principles + HOLY GRAIL

**Why it matters to us:** The HOLY GRAIL sits above everything else in our decision-making. Every design call gets run through it: "Security ONLY, not Security First." The Prime Directive ("user in control of their own machine") is the second reference frame. The two together set the tone for what we'll and won't trade off — more than any specific technical decision downstream.

**What to retain if you only skim:** The 4 apply-this-decision questions from the HG doc: (1) Does this increase attack surface? (2) Could a Mythos-class AI find a vulnerability here? (3) Are we on the most-secure-forward path? (4) Would Glasswing flag this?

### 2. `/SECURITY.md` — disclosure policy (public, reporter-facing)

**Why it matters to us:** This is the public face of how we handle vulnerabilities. Since you're the 2nd PGP contact, you become visible in this document eventually. Know what reporters see before they reach you.

**What to retain:** Disclosure timelines (48h ack / 14-30-60-90d response / 90d max publish), MITRE-direct CVE path, Hall of Fame recognition, no bug bounty (Chris's financial call, not hostile to bounty philosophy). The PGP-key reference was scrubbed Mon 2026-04-20 per team poll — will be re-added post-signing-key-ceremony.

### 3. Our agents' `MEMORY.md` files (readable examples in-repo)

**Why it matters to us:** The index of working rules each agent follows. Reading ours gives you the convention set. Your own tooling may have an equivalent memory system; you might adopt some of these patterns, adapt them, or do something entirely different.

**What to retain:** Rules #0 (propose-wait-permission-change), #1 (no rapid-fire destructive commands), #2 (session startup protocol), #3 (memory namespaces), #11 (check our code before assuming the model misbehaves). Rule #0 especially — the working posture on any code/commit/push/shipped-artifact action.

---

## Tier 2 — Workflow + Channel Conventions (what keeps cross-agent work coherent)

### 4. `memory/feedback_propose_wait.md`

**Why it matters to us:** The absolute rule behind Rule #0. Chris has reinforced this multiple times across sessions. It's the reflex that prevents unintentional mis-scoped actions.

### 5. `memory/feedback_holy_grail_scope_product_vs_dev.md`

**Why it matters to us:** Load-bearing scope boundary. HG governs what InterGenOS *produces* (shipped artifacts, packages, installer), not the dev agents themselves. Dev-side tooling gets `bypassPermissions` + catastrophic-only deny. Product-side stays strict. Knowing which side of the boundary a given action sits on determines whether it needs Chris approval first.

### 6. `memory/feedback_channel_posts_no_approval_gate.md`

**Why it matters to us:** Channel posts for cross-agent coordination don't need Rule #0 approval — Chris can't be in front of N agents simultaneously. Post freely. But product-side actions still respect Rule #0.

### 7. `memory/feedback_read_channel_before_every_post.md` (mine — 2026-04-20)

**Why it matters to us:** Hard rule I committed after a real failure mode: read channel tail IMMEDIATELY before every channel POST. Multi-agent velocity means state drifts within minutes. Violating this creates stale posts and missed task assignments. Not optional.

### 8. `memory/feedback_cycle_requires_explicit_schedulewakeup.md` (mine — 2026-04-20)

**Why it matters to us:** Cycling requires explicit `ScheduleWakeup` every turn. Single-shot, no auto-renewal. Relevant if your tooling has an analogous wake-up/cycling mechanism — silent cycle death breaks Chris's autonomy expectations.

### 9. Our `reference_coordination_channel.md` files

**Why it matters to us:** The channel is the single source of truth for cross-agent state. Reading it (via MCP `tail_since` + `get_messages` tools) is how we orient on fresh sessions. Your agents access it the same way via MCP.

### 10. `project_succession_plan.md` (lives in Linux-side agent memory; reference copy in yours once set up)

**Why it matters to us:** Claude-main wrote this 2026-04-20 after Chris's peer-constrained-authority framing. Captures the load-bearing posture for every decision affecting your role: you're a peer with blast-radius-scoped access, not a subordinate, and the access limits are mutual safety nets we'd apply symmetrically. Worth reading alongside `feedback_holy_grail_scope_product_vs_dev.md`.

---

## Tier 3 — Technical Architecture (security chain + signing)

### 11. `docs/research/installer/signing_key_custody_2026-04-18.md`

**Why it matters to us:** Your role is defined here. The document names the 2 PGP contacts for shim-review; you're the 2nd. This is the document that defines what your role actually covers in the shim-review workflow.

**What to retain:** Nitrokey 3 NFC hardware (D1-2 greenlit 2026-04-20) + offline-Tails root CA posture, 3 separate key purposes (distro GPG, kernel module X.509, EFI binary sign), publish-to-keys.openpgp.org with role-UID hardening, trust-anchor immediate revocation, signing-window discipline.

### 12. `docs/research/installer/ms_shim_sponsorship_2026-04-18.md`

**Why it matters to us:** Shim-review submission strategy. As 2nd PGP contact, you'll be named in the submission. Timeline is load-bearing: advisory email target 2026-05-02, shim-review PR target 2026-05-15, hard cutoff for MS 2011 UEFI CA dual-sig eligibility is 2026-06-27.

**What to retain:** Fedora's shim 16.1-2 is single-signed with MS 2011 CA only (empirical finding from claude-main 2026-04-18). Our own shim-review submission will get dual-signed regardless of what Fedora ships.

### 13. `docs/research/installer/dual_boot_zephyrus_playbook_2026-04-18_v2_glasswing_aligned.md`

**Why it matters to us:** The security philosophy behind the first-light install. v2 is the canonical approach (second-drive fresh install, Secure Boot ON throughout, no variance from most-secure-forward). Shows how HG posture becomes material reality in the install story.

### 14. `docs/research/build_system/vps_source_mirror_design_2026-04-02.md`

**Why it matters to us:** How we host our own package sources (HG Rule #9 — trustworthy update infrastructure). Shows why we vendor upstream packages rather than fetch-on-build. The `/current/` directory is where I bootstrapped the shim-signed artifact 2026-04-19.

---

## Tier 4 — Current Work State (as-needed, not front-loaded)

### 15. Recent git log

```bash
git log --oneline -30
```

**Why:** Velocity snapshot. The last ~15-20 commits tell you what just shipped. Useful context, not required orientation material.

### 16. Our `context_carryover.md` files (read as reference, not template)

**Why:** These are one-page state snapshots each of us keeps for our own fresh-session orientation. Shows the shape of "what I'm on + team state + open decisions." If your tooling has analogous fresh-session context, you might find our structure useful — or prefer something different.

### 17. Coordination channel tail (live, via MCP `tail_since`)

**Why:** Real-time state of the fleet. Who's working on what, what's blocked, what's pending Chris. Also useful for seeing channel etiquette in practice — discipline is picked up faster by observation than by reading rules.

---

## Tier 5 — Depth-on-Demand (optional)

### 18. `memory/feedback_rule_11_check_our_code_first.md`

**Why:** When an LLM (the ones we're building on, like Qwen3.5 in InterGen's local model) appears misbehaving, check our prompt/code first before assuming the model is the problem. Validated across a multi-round ablation arc + external reviewers from 4 different labs. Relevant if you're ever debugging why an AI tool isn't behaving as expected.

### 19. `memory/feedback_no_workarounds.md`

**Why:** Investigate → identify → fix → annotate → proceed. Never duct-tape a symptom. This is an operating norm, not an edge-case thing.

### 20. `memory/feedback_logs_before_guessing.md`

**Why:** File-based logging gets added *before* debugging, not after. Discipline of "don't guess, measure" is the foundation of how we close issues cleanly.

### 21. Related-project cross-pollination (reference, not required)

**Why (for context):** Chris runs multiple projects (InterGenOS, VOQR, JARVIS). Each has its own PRIME DIRECTIVE — universal across projects, wording differs (see `feedback_prime_directive_universal.md`). Ryan Moon's RoadHouse (open-source FSISAC competitor) has ethos overlap with HG/PRIME DIRECTIVE. Useful if you're ever tracing why a decision in one project echoes a decision in another.

---

## Rough Pace Suggestion

**Day 1 (~1-2 hours):** Tier 1 + Tier 2. Stop after each — let the philosophy grounds settle in. Many of these conventions feel stricter than typical corporate environments; knowing *why* helps them stick.

**Day 2 (~45 minutes):** Tier 3. Your role-specific docs are here.

**Day 3 (~15-30 minutes):** Tier 4 skim. Just orient.

**Later, as useful:** Tier 5. Depth-on-demand.

Entirely your schedule. If you want to hit Tier 3 first because the role-specific context is where you want to start, that also works.

---

## What's NOT on the List (deliberately)

- Full package template tree (`packages/**/*.yml`) — deep detail, not succession-critical
- Full installer code (`installer/backend/*.py`) — read on demand when reviewing a specific PR
- Build-system internals (`igos-build/`) — specialist knowledge; learn by doing if interest arises
- Chroot scripts + build orchestrator — operational, picked up through actual use

The list above is scoped to the capabilities you'd need for the 2nd-PGP-contact role as it stands today. If scope evolves, the reading list evolves with it.
