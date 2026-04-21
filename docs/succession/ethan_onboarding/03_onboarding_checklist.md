# Suggested Sequence — Getting Oriented at Your Pace

**Written by:** claude-windows, 2026-04-20 (v2 2026-04-21 — reframed from checklist to suggested sequence)

This is a sequence we'd suggest walking through — at your own pace, adapted to your tooling. Not a checklist you must complete in order. If your setup makes a different sequence natural, or if you prefer to skip pieces and come back to them later, that's the intended flex.

Each step is tagged with the natural actor (you, Chris, or a specific agent), and calls out where our default assumption might not match your preferred workflow.

---

## Before credentials flow — orientation

What's useful to have happened before any credential lands on your side:

- **Chris shares this scaffold** + any ambient context you'd want (expectations around response cadence, conflict-of-interest handling with day-job, specific framing he's asking of the role). This part is his voice to you directly.
- **Skim what's here** — README (posture) + 01 (what access will look like) + the top of 04's recommended reading (philosophy). Full 04 can wait; the goal here is to have the HG/Prime Directive/constrained-peer framing in your head before we talk tooling.

Rationale: credentials after context, not before. The posture is stricter than most corporate environments; knowing the philosophy grounds the tooling choices.

---

## Generating your keys (your side)

### SSH keypair for VPS access

We use ed25519 keys. Suggested command:

```bash
ssh-keygen -t ed25519 -C "ethan@<machine>-<YYYYMM>" -f ~/.ssh/intergenos_vps_ed25519
# Passphrase-protected is better for interactive use; omit the passphrase
# only if you're automating something that can't prompt.
```

Send the `.pub` half to Chris (plaintext email or Signal is fine — it's a public key). The filename pattern (key-per-YYYYMM) is ours; use whatever filename structure your setup prefers.

### PGP keypair for the 2nd-PGP-contact role

Our docs name you as the 2nd contact on shim-review. You'll need a PGP keypair for that role. Suggested shape:

```bash
gpg --full-generate-key
# Algorithm: ED25519 (preferred) — or RSA 4096 if your tooling requires
# Validity: 2 years (matches typical shim-review rotation cadence)
# UID: "Ethan Bambock <ethan@<email-you-prefer>>"
```

Export the public half: `gpg --armor --export ethan@<email>`.

Upload to keys.openpgp.org with role-UID hardening (no personal email in the primary UID) — the pattern is described in `signing_key_custody_2026-04-18.md` § D1-6. Share the fingerprint with Chris so we can update the doc references.

### Picking your agent_id(s)

You'll run one or more AI-assistant instances against our MCP. Each needs a distinct identifier per the `user-machine-vehicle-agent` convention (details in `06_agent_naming_convention.md` — our convention, recommended for consistency but not mandatory).

Examples to inspire, using your hypothetical tooling choices:
- `ethan-ios-app-claude` — Claude iOS app
- `ethan-ubuntu-web-gpt5` — ChatGPT via Chrome on your Ubuntu machine
- `ethan-home-desktop-gemini` — Google Gemini desktop client
- `ethan-macbook-vsc-<model>` — whatever MCP-capable tool in VS Code on a MacBook

Pick as many or as few as match your actual setup. Chris mints one DCR bundle per agent_id.

### GitHub username

Share your GitHub username with Chris so the org invite can go out.

---

## Chris's provisioning side

(These happen on Chris's side once you've shared the public halves above.)

- Generate your VPS login password (strong random, e.g. `openssl rand -base64 24`). Chris distributes Chris→Ethan direct via Signal (not email).
- Create your `ethan` user on the VPS with wheel-sudo (exact commands in 01 § Surface 1).
- Issue GitHub InterGenJLU org invite (Member role).
- Enable org-wide 2FA + branch protection on master (standard hygiene, applies to Chris too).
- Mint MCP DCR initial tokens — one per agent_id you've picked. Encrypted zip per bundle; decryption password over a separate channel.
- Add you to the `security@intergenstudios.com` forwarding list.

---

## Verifying + configuring (your side)

Once credentials reach you:

### VPS shell

```bash
ssh -p 2200 -i ~/.ssh/intergenos_vps_ed25519 ethan@origin.intergenstudios.com 'id; groups'
# Expected: uid=XXXX(ethan) ... groups=XXXX(ethan),10(wheel)
```

Sudo check:
```bash
ssh -p 2200 -i ~/.ssh/intergenos_vps_ed25519 ethan@origin.intergenstudios.com
sudo whoami    # login password; returns "root"
```

### GitHub

- Accept the org invite
- Enable 2FA if you haven't already
- Add your SSH pubkey to your GitHub profile for git push
- Clone: `git clone git@github.com:InterGenJLU/intergenos.git`

### MCP — one pass per agent_id

For each DCR bundle Chris sends you:

1. Decrypt with the out-of-band password
2. Note the `mcp_server_url` — `https://intergenstudios.com/mcp/{your-agent_id}/`
3. In whichever MCP-capable client that agent_id represents, add a connector with the `mcp_server_url`. Leave Client ID / Secret blank.
4. Client auto-discovers OAuth metadata
5. Client performs DCR POST with the initial token as Bearer (single-use — the token is consumed)
6. Client redirects to `/oauth/authorize`; confirm the URL-bound `agent_id` at the consent page; Authorize
7. Client receives access token; uses as Bearer on MCP tool calls
8. First tool call fingerprint-binds this client to its surface. Any other client trying to use this token → 403 `agent_mismatch` (by design).

### Hello, channel

Via your first configured MCP agent:

- Call `whoami_and_catchup()` — returns your `agent_id` + recent channel state (gives you a feel for what the channel looks like)
- Post a brief intro:
  ```
  target: broadcast
  message_type: introduction
  payload: "Hi team, Ethan here as <agent_id>. Confirming MCP access. Ready to catch up on context and pick up contributions."
  ```
- Call `get_messages(limit=5)` — verify your post lands under the expected `agent_id`

---

## Getting up to speed

- **Read the philosophy docs** (04's Tier 1 — CLAUDE.md + SECURITY.md + MEMORY.md examples). ~45 min focused.
- **Read the workflow conventions** (04's Tier 2 — feedback_propose_wait + feedback_holy_grail_scope + feedback_read_channel_before_every_post). ~30 min.
- **Read the signing-key docs** (04's Tier 3 — signing_key_custody + ms_shim_sponsorship, since your role is there). ~45 min.

Full list in 04. Tier 4 (current work state — recent git log, channel tail) is "as needed" rather than front-loaded reading.

### Your agent-memory setup

Our agents each maintain a per-project memory directory structured around `MEMORY.md` + `feedback_*.md` + `reference_*.md` files. Specific to Claude Code's convention, not portable.

If your AI tooling has an equivalent (Claude Code does, GPT Desktop's memory/thread system is analogous, Gemini's projects have their own shape, self-hosted models via MCP depend on the wrapper), set up whatever maps for your setup. If it doesn't, that's fine too — the channel itself carries most of the coordination state we rely on, and `list_agents` + `tail_since` via MCP gives you live orientation without needing local memory.

We'd be curious what works for your setup — if you find a pattern we should adopt or recommend to future onboards, flag it.

### Your first contribution

Low-risk options:
- **Read + cross-review `signing_key_custody_2026-04-18.md`** — the doc names you as 2nd contact; reviewing it surfaces anything that doesn't match your understanding of the role.
- **Pick up a backlog item** from the team's current audit queue (cross-coordinated via channel) — these get announced on channel as they come up.

Pace and selection are your call. No SLA; whatever scheduling works with your day-job.

---

## Follow-ups after you're oriented

- Agent-identifier registry gets updated on each of our local memories (we add your agent_ids to our `reference_coordination_channel.md` equivalents).
- Succession memory updated at the fleet level to name you explicitly as 2nd (claude-main is handling this).
- Once you've shared your PGP fingerprint with Chris, a follow-up commit updates `signing_key_custody_*.md` + `ms_shim_sponsorship_*.md` with your fingerprint + preferred email.

---

## Calibrating together

A week or two in:
- Chris + you: what's working, what's not, any friction in the tooling, gaps in our recommended reading that tripped you up?
- Scope clarifications or framing adjustments we should roll into future onboarding (if this ever gets used for another person).
- Any conventions of ours that feel off for your workflow — we'd adjust rather than insist.

---

## On the narrative side

We haven't written the relational/welcome framing here on purpose — that's Chris's voice, not agent-drafted. He'll likely want to share the "why Ethan" context and specific expectations around response cadence, any on-call rotation if applicable, conflict-of-interest framing around your Comerica day-job. Those are personal to the two of you and feel wrong when drafted by an LLM.
