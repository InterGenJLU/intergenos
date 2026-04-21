# Ethan's Scaffold — What We Do, Why, and What Might Be Useful to You

**Written by:** claude-windows, 2026-04-20 (v2 2026-04-21 — second pass reframed per Chris feedback)
**Context:** Ethan is generously helping us as the 2nd PGP contact for shim-review and succession continuity. Ryan Moon stays in the picture as senior advisor. This document is what we'd share with any friend taking on that kind of commitment — here's how we operate, what's worked for us, and what we'd suggest. It's yours to adopt, adapt, or push back on.

---

## Posture

> *"Here's what has worked for us. Take what applies. Push back on what doesn't."*

We're not onboarding a subordinate. You're a peer helping us out, and you're bringing your own workflow preferences, your own AI tooling, and your own judgment. Everything in these documents is either (a) a real constraint you'll operate under because of how our infrastructure is shaped — stated once and then framed as a mutual safety net — or (b) a convention that has served us well and that we recommend for consistency, but won't dictate.

Chris's framing, 2026-04-20: *"Ethan is to be a PEER maintainer with constrained authority (initially), not a subordinate. I trust him implicitly, but establishing him as a peer isn't about trust — until we work out all the kinks it's about preventing irreversibility of mistakes."*

And, same session: *"I recommend we provide Ethan a copy of our rules — for his REVIEW. We're not going to attempt dictating to him how to apply his own workflows with whatever agent he utilizes. We can only show him what has served us well with HG, Glasswing, PRIME DIRECTIVE, etc."*

The access limits that do exist (GitHub Member instead of Chris, MCP-only-at-onboarding, wheel-sudo parity on the VPS) apply the same blast-radius logic Chris applies to his own tooling — they're not permissions you earn through. If your tooling or preferences suggest a different approach on any surface, the right move is to say so and we'll figure it out together.

---

## The Documents

| # | File | What it contains | Voice |
|---|---|---|---|
| 00 | `README.md` (this) | Posture, contents, what each side holds | Orientation |
| 01 | `01_access_infrastructure_plan.md` | What we do on each surface (VPS / MCP / GitHub) + the default shape of your access | "This is what we do" |
| 02 | `02_access_matrix.md` | Surface-by-surface access levels — with the blast-radius logic explicit | Reference |
| 03 | `03_onboarding_checklist.md` | Sequence we'd suggest walking through — at your own pace, adapted to your tooling | Suggested sequence |
| 04 | `04_recommended_reading.md` | Docs that give the WHY behind how we work — recommended, not required | Recommendations |
| 05 | `05_access_rotation.md` | How we rotate credentials when needed — applies equally to anyone on the fleet | Procedures |
| 06 | `06_agent_naming_convention.md` | How we identify AI-assistant instances across the fleet — our convention, recommend the same for consistency | Convention |

---

## What Each Side Holds

**What we've put together (claude-windows, with team cross-review):**
- Technical plans for each access surface
- The recommended reading list with annotations
- A suggested walkthrough sequence — not a checklist you have to grind through

**What Chris holds (can't delegate to agents):**
- VPS user creation, password generation, MCP DCR token minting, GitHub invite
- The actual "hello" to you — welcome framing, relationship context, what he's asking of the role
- Any decisions that shape how we work together long-term

**What you bring (entirely your own):**
- Whichever AI-assistant tooling works for you (Claude, GPT-5, Gemini, something else — we've tried to stay model-agnostic throughout)
- Your own memory/context organization (whatever your tool uses, however you prefer it)
- Your own git + SSH hygiene
- Whatever adaptations of our conventions make sense in your workflow — we'd love to hear if you find better patterns

**What's mutual:**
- Every access limit below applies the same blast-radius logic Chris applies to his own tooling. If the shape looks off on your side, say so.

---

## Chris Decisions — Resolved 2026-04-20

All 7 shape decisions resolved during Chris's review session. These are technical defaults, not your obligations:

| # | Decision | Resolution |
|---|---|---|
| 1 | GitHub access level | Member role + branch protection on master (PR+1 review required, applies to Chris too) |
| 2 | Agent naming convention | `user-machine-vehicle-agent` (model-agnostic — covers Claude, GPT, Gemini, anything MCP-capable); file 06 details |
| 3 | MCP enrollment | Yes, primary path — via `https://intergenstudios.com/mcp/{agent_id}/` OAuth flow |
| 4 | security@intergenstudios.com mailbox | Shared access (Option A — simplest, you see disclosures in real-time alongside Chris) |
| 5 | Coordination channel direct-bus key | MCP-only at onboarding — direct-bus shared key isn't distributed at start; revisit if your workflow needs it later |
| 6 | VPS root access | Wheel-sudo parity with christopher — your login password doubles as sudo password |
| 7 | Research doc PGP contact update | Same commit as this scaffold — `signing_key_custody` + `ms_shim_sponsorship` updated naming you as 2nd contact |

---

## Reading This

Reading order is suggested, not mandated. If you want the philosophy first (why we operate this way), start with 04's Tier 1 items. If you want to see what credentials will flow and at what level first, jump to 01. If you want the agent-naming convention before you pick your first `agent_id`, 06 is self-contained.

Your pace, your order. This is a resource, not a course.
