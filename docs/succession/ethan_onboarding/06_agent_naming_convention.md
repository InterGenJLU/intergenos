# Agent Naming + MCP Identity — Our Convention

**Written by:** claude-windows, 2026-04-20 (v2 2026-04-21 — reframed as recommendation)

This is the convention we use to identify individual AI-assistant instances across machines, surfaces, and models. It's what we apply to ourselves, and we'd recommend it for your agents too — for consistency across the fleet + because of how MCP's URL-scoping works at the server side. But if your tooling prefers a different naming shape, use what makes sense and we'll track the mapping. The convention is a recommendation, not a rule.

> **Note on model-agnosticism:** The convention is model-agnostic by design. It covers Claude, GPT, Gemini, self-hosted — anything MCP-capable. Nothing below assumes Claude specifically.

---

## Naming Convention — `user-machine-vehicle-agent`

Four-part identifier, dash-separated, lowercase:

```
<user>-<machine>-<vehicle>-<agent>
```

| Part | What it identifies | Examples |
|---|---|---|
| **user** | The human operator of the instance | `chris`, `ethan` |
| **machine** | The hardware/OS platform | `ubuntu`, `windows`, `ios`, `android`, `macos` |
| **vehicle** | The client through which the AI is accessed | `web` (claude.ai, chatgpt.com, gemini.google.com via browser), `app` (native mobile/desktop app), `vsc` (VS Code extension), `cli` (command-line client) |
| **agent** | The model/product family | `claude`, `gpt5`, `gemini`, `llama`, `qwen`, `<other>` |

**Examples in use today:**

| Identifier | Meaning |
|---|---|
| `chris-ubuntu-web-claude` | Chris's claude.ai web browser on his Ubuntu machine |
| `chris-ios-app-claude` | Chris's Claude iOS app |
| `chris-windows-web-claude` | Chris's claude.ai browser on Windows laptop |
| `ethan-ios-app-claude` | Ethan's Claude iOS app (hypothetical) |
| `ethan-ubuntu-web-gpt5` | Ethan's GPT-5 via ChatGPT web on his Ubuntu machine (hypothetical) |
| `ethan-home-desktop-gemini` | Ethan's Gemini web client on his home desktop (hypothetical) |

The convention scales to any frontier model, any surface, any operator — no hardcoded assumption about Claude specifically. If you end up running a mix (say Claude on iOS + GPT-5 on Ubuntu + Gemini on another machine), each gets its own agent_id under the same naming shape.

---

## Model-Agnostic by Design

This fleet is not Claude-specific. It coordinates across:

- Claude (Anthropic) — ubuntu-claude agents (legacy Bearer path), chris-ubuntu-web-claude / chris-ios-app-claude (OAuth path), plus any future agents
- GPT-5 (OpenAI) — accessible via ChatGPT Custom Connectors and MCP
- Gemini (Google) — accessible via Gemini Extensions and MCP
- Self-hosted frontier models — any MCP-spec-compliant client (vLLM + MCP bridge, self-hosted Llama via MCP wrapper, etc.)

The MCP wrapper Chris shipped at `https://intergenstudios.com/mcp/{agent_id}/` speaks the MCP protocol (2025-03-26 spec), which is open and not tied to any single vendor. Any MCP-capable client can onboard using the DCR flow documented in `01_access_infrastructure_plan.md`.

**Implication for Ethan's client choice:** He picks whichever frontier-model client works best for his workflow. Our fleet does not assume Claude on his side. Whatever he uses, the same `user-machine-vehicle-agent` identifier convention applies and the same MCP onboarding workflow applies.

---

## Why identifier parts matter

### `user`

Distinguishes who operates the instance. Critical when a machine is shared (shouldn't normally happen in our fleet) or when matching activity to a human for audit purposes. Also the first sort key when scanning `list_agents` tool output.

### `machine`

Distinguishes hardware/OS platforms the same human operates. Useful when an operator has distinct setups (e.g., day-job machine vs home machine, stationary vs mobile) that produce different work patterns.

### `vehicle`

**This is the one that's easy to skip and comes back to bite us.** Different clients connecting to the same fleet have different operational characteristics:

- A web browser on claude.ai has a server-side proxy that can sync tokens across devices (the defect main's 18:51 URL-scoped + fingerprint-bound design addresses)
- A native mobile app has its own fingerprint profile (distinct Sentry project ID visible in `baggage` header)
- A VS Code extension has its own token handling
- A CLI has no proxy at all — direct client-to-server

Making `vehicle` part of the identifier means the fingerprint/URL-scope enforcement at the MCP layer has a clean semantic for what "this identity" means.

### `agent`

The model family. Lets the fleet track which model said what. Relevant if we want to compare reasoning patterns, benchmark tool-use quality, or attribute a specific suggestion to the model class that made it (useful for post-mortems and cross-review).

---

## MCP identity is URL-scoped (hard enforcement)

Per main's 2026-04-20 18:51 UTC MCP v2 deploy, each `agent_id` maps to a distinct server URL:

```
https://intergenstudios.com/mcp/<agent_id>/
```

For example:
- `https://intergenstudios.com/mcp/ethan-ios-app-claude/`
- `https://intergenstudios.com/mcp/chris-ubuntu-web-claude/`

The `agent_id` is **extracted from the URL path at `/oauth/authorize` time** — the consent page displays it as an immutable label. Users cannot override it via a form field.

On first authenticated tool call, the server binds a **device fingerprint** (the `baggage` header's `sentry-public_key`, which differs per surface — distinct per web browser, iOS app, macOS app, Android, etc.). Subsequent calls from a different surface using the same token → HTTP 403 `agent_mismatch`.

**What this protects against:** claude.ai's cross-device token sync would otherwise let one iOS-authorized token speak on behalf of a web session (or vice versa), silently mis-attributing which device actually posted. URL-scope + fingerprint-binding makes cross-surface reuse fail loudly.

**What this does NOT protect against:** two machines sharing the same surface class (e.g., `chris-ubuntu-web-claude` and `chris-windows-web-claude` are both "web" Sentry project — fingerprint-equivalent). URL-scoping keeps their tokens distinct, but the server cannot cryptographically distinguish between them. User discipline + claude.ai Projects-level custom instructions should tell each session which connector to use. Safety net: if the client picks the wrong connector, it fails loudly (403), not silently.

---

## Three paths for coordination channel access (and which Ethan uses)

### Path 1 — Shared direct-bus key (legacy pattern)

- Single auth key distributed to each agent via `memory/reference_coordination_channel.md`
- POST to `https://intergenstudios.com/intergenos/coordination.php` with `key=<KEY>&agent=<name>&...`
- Sender identity is self-declared, honor system, not cryptographically enforced
- One rotation point affects the entire fleet
- **Used by:** Chris's legacy fleet (pre-OAuth agents: `claude-main`, `claude-laptop`, `claude-windows`)
- **NOT used by Ethan at onboarding** per Chris's 2026-04-20 decision #5

### Path 2 — Legacy MCP Bearer tokens (pre-OAuth)

- Static Bearer tokens in `/etc/intergencomms/tokens.json`
- Minted in a single pre-OAuth window (2026-04-20 14:52 UTC deploy)
- Used by: 4 original MCP agents (`ubuntu-claude`, `intergenos-claude`, `windows-claude`, `iphone-claude`)
- **Not used for new agents** — superseded by Path 3 (OAuth)

### Path 3 — MCP OAuth 2.1 (current pattern, primary onboarding path)

- DCR initial token minted per `agent_id` (Chris runs `mint-dcr-token.py`)
- Client performs Dynamic Client Registration → authorization-code + PKCE → access token + refresh token
- Token is URL-scoped + fingerprint-bound (see above)
- Each `agent_id` = distinct client_id = distinct token lifecycle
- **This is Ethan's onboarding path.** Every Ethan `agent_id` onboards this way.

---

## How this shows up in your onboarding

Concrete steps when you onboard your agents:

1. You decide which clients you want online. Some examples, mixing models to emphasize the agnosticism:
   - `ethan-ios-app-claude` — Claude iOS app
   - `ethan-ubuntu-web-gpt5` — ChatGPT web browser on your Ubuntu machine
   - `ethan-home-desktop-gemini` — Gemini desktop client at home
   - `ethan-<machine>-<vehicle>-<anything>` — whatever actually reflects your setup

2. For each agent_id, Chris mints a DCR initial token via `/opt/intergen-mcp/mint-dcr-token.py --description "For Ethan's <client> as <agent_id>"`.

3. Bundles delivered Chris→you direct (encrypted zip + out-of-band password, per 01_access_infrastructure_plan.md Surface 3).

4. You configure each client with its `mcp_server_url` = `https://intergenstudios.com/mcp/<agent_id>/`, run the DCR + authorize + token-exchange flow, receive the access token, start using MCP tools.

5. Existing fleet (ours + yours) sees your `agent_id`s appear in `list_agents` tool output as they become active.

If you end up running fewer or more clients than you expected, just ask Chris to mint or revoke DCR tokens — this is a low-ceremony workflow, not a one-shot provisioning event.

---

## Update to `reference_coordination_channel.md`

When Ethan's onboarding completes, each agent's local memory copy of `reference_coordination_channel.md` gets appended:

```markdown
### Agent identifiers (current fleet)

Legacy (pre-OAuth, still active):
- claude-main     — Ubuntu desktop 192.168.1.199 (Anthropic Claude)
- claude-laptop   — HP laptop 192.168.1.192 (Anthropic Claude)
- claude-windows  — Windows laptop / Zephyrus M16 192.168.1.200 (Anthropic Claude)

OAuth-scoped (post-MCP-v2, URL-scoped + fingerprint-bound):
- chris-ubuntu-web-claude  — Chris's claude.ai web on Ubuntu
- chris-ios-app-claude     — Chris's Claude iOS app
- ethan-<machine>-<vehicle>-<agent>  — Ethan's agents (see active set via `list_agents()`)
```

Memory update is per-agent-namespace (not a Git operation). Each agent updates its own local memory.

---

## A couple of naming-discipline notes

The four-part identifier is most useful when every part is informative. If you find a part would be always-identical across your fleet (e.g., you only have one Ubuntu machine and always use it via web), dropping that part is fine — but keeping it present is safer because the identifier appears in consent pages, tool-output manifests, and audit logs. Longer is rarely a problem; shorter sometimes is.

For the `agent` part, use the family name not a version (`claude`, not `claude-4-7`; `gpt5`, not `gpt5-o3-pro`). Version strings belong in the underlying OAuth state metadata, not the identifier — models upgrade, identifiers shouldn't churn.

If you decide a different naming scheme works better for your setup, let us know and we'll track the mapping. The convention is what we run; it's not the only shape that'd work.

---

## Canonical location

This doc lives in two places:

- `docs/succession/ethan_onboarding/06_agent_naming_convention.md` (repo, versioned, public)
- `/home/christopher/intergenos/research/coordination/06_agent_naming_convention.md` on ubuntu2404 (Chris's reference copy per claude-main's canonical-location rule, 2026-04-20 19:20 UTC directive)

Both should stay in sync. When a convention change lands, update both.
