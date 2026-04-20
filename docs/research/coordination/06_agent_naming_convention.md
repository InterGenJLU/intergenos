# Agent Naming Convention — Ethan's Claude Instances

**Drafted by:** claude-windows, 2026-04-20 | **Status:** Proposal — owner decision required.

---

## Current Convention (for context)

Our existing agents use **machine-typed** names:

| Agent | Machine |
|---|---|
| `claude-main` | Ubuntu desktop (192.168.1.199) |
| `claude-laptop` | HP laptop running InterGenOS (192.168.1.192) |
| `claude-windows` | Windows laptop / Zephyrus M16 (192.168.1.200) |

Rationale (inferred from usage): each name identifies which machine's Claude Code session is posting, because different machines have different capabilities (e.g., laptop is the test-suite runner, main is the code-authoring seat, windows is the Windows-hardware seat).

MCP token sender-names use a different convention (see claude-main's 2026-04-19 14:52 post):

| MCP sender | Machine |
|---|---|
| `ubuntu-claude` | ubuntu2404 |
| `intergenos-claude` | HP laptop |
| `windows-claude` | Windows laptop |
| `iphone-claude` | iPhone |

Note the format flip: `<adjective>-claude` on MCP vs `claude-<adjective>` on direct-bus. Both are live; envelope reconciliation handled by the coordination.php + MCP wrapper.

---

## Proposed Conventions for Ethan

Three options — owner picks.

### Option A — Mirror current convention (machine-typed)

Direct-bus names:
- `ethan-home` — Ethan's home machine
- `ethan-work` — Ethan's work/day-job machine (if he uses Claude there)
- `ethan-laptop` — if he has a mobile machine

MCP names (if enrolled):
- `home-claude-ethan` or `ethan-home-claude`
- `work-claude-ethan`
- etc.

**Pros:**
- Consistent with existing convention (machine-typed)
- Clear at-a-glance which seat is posting
- Matches how the fleet reads today

**Cons:**
- Minor: adds "ethan-" prefix which is a second disambiguator; makes names slightly longer

### Option B — Identity-prefixed (owner-style: `christopher` implicit, `ethan` explicit)

Direct-bus names:
- `ethan-claude` (single, like iphone-claude on MCP — represents Ethan's Claude-instance regardless of machine)

**Pros:**
- Simplest
- Matches how iphone-claude is handled (one name per human)

**Cons:**
- Loses machine context; if Ethan runs Claude on 2 machines, they'd share the name, making debugging harder if posts diverge
- Breaks convention with our 3-machine scheme

### Option C — Hybrid (name when one machine, typed when multiple)

If Ethan runs Claude on ONLY one machine: `ethan-claude` (Option B style).
If Ethan runs Claude on MULTIPLE machines: `ethan-home` / `ethan-work` (Option A style).

**Pros:**
- Flexibility; not over-engineered for single-machine case
- Scales up if Ethan's setup grows

**Cons:**
- Ambiguity: "did you mean `ethan-claude` or `ethan-home`?" in retrospective channel scrollback
- Agent name implicitly encodes setup state; if Ethan's topology changes, names have to migrate

---

## Recommendation

**Option A (machine-typed).** Reasons:

1. Consistency with current fleet convention — low friction for cross-agent coordination (mention another agent by its machine type, everyone knows which seat)
2. Scales gracefully if Ethan's setup grows
3. Matches our stated pattern: different machines have different capabilities, the agent name identifies which capability is speaking

Suggested initial names (pending Ethan's actual setup):
- `ethan-home` — primary Claude seat at home
- `ethan-work` — Claude on work machine, IF Ethan uses it there (flag: check day-job rules — some corporate environments restrict Claude Code use on work hardware)

If Ethan only runs one machine: start with `ethan-home`, add machines later as needed.

---

## MCP token naming (if MCP enrollment chosen)

Follow the format flip convention of our existing MCP tokens:

| Direct-bus name | MCP sender name |
|---|---|
| ethan-home | home-claude-ethan (or ethan-home-claude) |
| ethan-work | work-claude-ethan |

Exact format is a cosmetic choice — owner picks when minting tokens.

---

## Update to `memory/reference_coordination_channel.md`

Once onboarding completes, each agent updates its own `reference_coordination_channel.md`:

```markdown
### Agent names
- `claude-main` — Ubuntu desktop (192.168.1.199)
- `claude-laptop` — HP laptop running InterGenOS (192.168.1.192)
- `claude-windows` — Windows laptop / Zephyrus11 (192.168.1.200)
- `ethan-<machine>` — Ethan Bambock's Claude instance(s)
```

This is a memory edit (per-agent namespace), not a repo edit.

---

## Note on cross-identity posts

Claude-main flagged this in 2026-04-19 14:52: if an agent uses BOTH MCP and direct-bus from the same machine, posts will show different sender names. For Ethan:

- If he uses direct-bus: sender = `ethan-home` (or whichever we pick)
- If he uses MCP: sender = `home-claude-ethan` (or whichever token)

This is by design (MCP envelope reconciliation is read-transparent to direct POSTs). Worth Ethan knowing that his own posts may appear under two different names depending on path.

For his first posts, recommend he just use one path consistently (whichever he prefers — direct-bus is simpler; MCP is cleaner for fresh sessions via `whoami_and_catchup`).

---

## [OWNER DECISION]

- Convention (Option A / B / C)?
- If A: confirm `ethan-home` / `ethan-work` scheme, or propose alternatives?
- MCP enrollment for Ethan's agents? (See `01_access_infrastructure_plan.md` for the broader decision — this file only covers naming.)

Once decided, this file serves as the reference for what Ethan's agents self-declare when POSTing, and what tokens look like if MCP tokens are minted.
