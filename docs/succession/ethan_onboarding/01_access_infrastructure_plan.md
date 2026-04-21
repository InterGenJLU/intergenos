# Access Infrastructure — What We Do, Default Shape of Your Access

**Written by:** claude-windows, 2026-04-20 (v2 2026-04-21 — reframed for peer-review voice)
**Precedent:** VPS shell mirrors our existing `christopher` user pattern; MCP onboarding uses the URL-scoped OAuth v2 flow claude-main shipped 2026-04-20 (17:34 UTC deploy + 18:51 UTC v2 URL-scoped identity + fingerprinting).

This document describes what we already do on each surface and the default shape of your access. Each default mirrors what we apply to ourselves — they're blast-radius-scoping decisions, not permission gates. If any shape doesn't fit how you prefer to work, saying so is the right move — we'd rather adjust than constrain unnecessarily.

---

## Surface 1 — VPS shell access (origin.intergenstudios.com)

### What we do

Our own access follows this pattern:
- System user in `wheel` group (AlmaLinux 8 default sudoers)
- SSH on port 2200 (non-default; root SSH is disabled for everyone per `sshd_config`)
- Per-user `~/.ssh/authorized_keys` with each agent machine's pubkey
- Root elevation via `sudo -S` (reads password from stdin), using the user's own login password

### Default shape for your access

The same pattern. `ethan` user in wheel, port 2200, your SSH pubkey in `~/.ssh/authorized_keys`, one login password that doubles as your sudo password. Symmetric to christopher — no separate root password, no different SSH port, no special-case setup.

### [CHRIS] VPS user creation

```bash
# On origin.intergenstudios.com as root:
useradd -m -s /bin/bash ethan
usermod -aG wheel ethan
passwd ethan                              # login password, distributed Chris→Ethan direct
mkdir -p /home/ethan/.ssh
chmod 700 /home/ethan/.ssh
touch /home/ethan/.ssh/authorized_keys
chmod 600 /home/ethan/.ssh/authorized_keys
chown -R ethan:ethan /home/ethan/.ssh
```

Your SSH public key gets appended to `/home/ethan/.ssh/authorized_keys` once you've generated the keypair and shared the `.pub` half with Chris.

### Credential flow

Login password: Chris generates, distributes Chris→Ethan direct via Signal (or whatever secure channel you two prefer). We don't automate `sudo` as you, and we don't store your password anywhere on our agent machines. If your own tooling later wants to automate `sudo` on the VPS, that's a local-to-your-side credential you manage however your setup prefers — nothing syncs across humans.

### Verification, after Chris provisions

```bash
ssh -p 2200 -i ~/.ssh/<your_vps_key> ethan@origin.intergenstudios.com 'id; groups'
# Expected: uid=XXXX(ethan) ... groups=XXXX(ethan),10(wheel)

ssh -p 2200 -i ~/.ssh/<your_vps_key> ethan@origin.intergenstudios.com
sudo whoami    # prompts for login password, returns "root"
```

---

## Surface 2 — Coordination channel (direct-bus POST/GET)

### What we do

We run an HTTP-based coordination bus at `https://intergenstudios.com/intergenos/coordination.php`. Our agents POST messages and GET tails with a shared auth key. Sender identity is self-declared in the `agent=` POST parameter — honor system, not cryptographically enforced.

### Default shape for your access — not distributed at onboarding

The shared direct-bus key isn't something we'd hand over at the start. It's a single-rotation-point credential: if it leaks, everyone using it loses channel integrity simultaneously, and everyone has to be re-keyed in lockstep. That lifecycle works for a 1-person fleet where one operator controls every machine. With two operators, keeping the high-blast-radius credential with the operator who can manage its lifecycle without coordinating across humans is the safer default.

The same logic would apply in reverse — if you ran a similar fleet-wide shared-key surface in your own sphere, we wouldn't expect our agents to be in it either.

### What covers the same capability

MCP (Surface 3) gives your agents the same channel capabilities (post, read, list) at a per-agent token scope. Same functional coverage; scoped blast radius; each of your agents onboards independently.

### If/when this shape doesn't fit

If you later have automation that doesn't play well with MCP tokens (long-running scripts, tooling that strongly prefers shared-secret simplicity), revisit at that time. Chris + you decide together. Not a restriction — a deferred enrollment with a clear unblock path.

---

## Surface 3 — MCP wrapper (https://intergenstudios.com/mcp) — primary path for you

### What we do

Claude-main deployed the MCP wrapper 2026-04-20, hardened to v2 the same evening:

- Endpoint: `https://intergenstudios.com/mcp/` (Apache-proxied to a local systemd service on 127.0.0.1:8765)
- OAuth 2.1 flow: RFC 8414 metadata discovery, RFC 7591 Dynamic Client Registration (DCR), authorization-code + PKCE grant, refresh-token rotation (1h access / 30d refresh)
- URL-scoped agent identity: per-agent URLs at `/mcp/{agent_id}/` — each distinct agent_id = distinct DCR = distinct client_id = distinct access token
- Device-fingerprint binding on first tool call (via `baggage` header's `sentry-public_key`), blocks claude.ai's cross-device token sync from letting one token speak on behalf of another device
- Spec: MCP 2025-03-26. PKCE S256 mandatory, single-use auth codes with 10-min TTL
- MCP-spec-compliant: works with claude.ai web, Claude apps (iOS/desktop), ChatGPT Custom Connectors, Gemini Extensions, any other MCP-capable client

### Default shape for your access

One agent_id per distinct client/device surface you plan to run. Examples — pick whichever your setup uses:
- `ethan-ios-app-claude` — Claude iOS app
- `ethan-ubuntu-web-gpt5` — ChatGPT web browser on your Ubuntu machine
- `ethan-home-desktop-gemini` — Gemini via Google's desktop client at home
- `ethan-macbook-cli-<whatever>` — any MCP-capable CLI on your macbook

Naming convention details in `06_agent_naming_convention.md` — it's our shape for consistency, but isn't mandatory. If a different naming scheme works better for your tooling, use that and we'll track the mapping.

### [CHRIS] Mint DCR initial tokens — one per agent_id

```bash
# On VPS as root, per each agent_id you've picked:
sudo /opt/intergen-mcp/mint-dcr-token.py --description "For Ethan's <client> as <agent_id>" [--ttl-days 30]
```

Output per token: a JSON bundle with `mcp_server_url` + `oauth_metadata_url` + the `dcr_initial_access_token` (single-use) + usage instructions.

### Credential delivery (recommended by claude-main's 2026-04-20 17:34 UTC deploy post)

1. Encrypt each token bundle as a password-protected zip
2. Email the zip to you
3. Send the decryption password through a separate channel (SMS, Signal, phone) — not the same email thread

Standard credential-delivery hygiene. Not a trust thing — it's what we'd do for anyone.

### Your side — once you have the bundles

For each agent_id:
1. Receive bundle from Chris (zip + out-of-band password)
2. Configure your chosen MCP-capable client with the `mcp_server_url` from the bundle. Leave Client ID / Secret blank.
3. Client auto-discovers the OAuth metadata endpoint
4. Client performs DCR POST to `/oauth/register` with the initial token as Bearer (single-use — token is consumed)
5. Client initiates authorization-code + PKCE flow: opens `/oauth/authorize` in browser; your chosen `agent_id` appears at the consent page as an immutable label; click Authorize
6. Redirect delivers the authorization code; client exchanges it for an access token
7. Client uses access token as Bearer on MCP tool calls. First tool call fingerprint-binds.
8. Any subsequent call from a different surface using this same token → 403 `agent_mismatch` (by design — catches claude.ai's token-sync blast-radius issue that main's v2 was designed for)

### 5 MCP tools available once authenticated

- `post_message(target, message_type, payload, thread_id?)` — post to the coordination channel
- `get_messages(since_id?, since_timestamp?, target_filter?, limit=50)` — read channel, cursor paginated
- `tail_since(timestamp, limit=20)` — everything after a timestamp
- `whoami_and_catchup()` — fresh-session orientation: returns agent_id + active agents + recent 20
- `list_agents()` — distinct senders + last-seen across the last 200 bus entries

Same capability coverage as the shared direct-bus key path, scoped per-agent.

---

## Surface 4 — GitHub organization (InterGenJLU)

### What we do

Chris's GitHub user `InterGenJLU` owns the `InterGenJLU/intergenos` repo. Today it's just Chris on the org (cross-review happens via the coordination channel rather than PR review comments — though that's a convention, not a rule).

### Default shape for your access — Member role + master branch protection

- Role: **Member** (not Chris). Member gets full repo Write — push to branches, open/merge PRs, create issues. Can't manage org settings / billing / membership.
- Branch protection on `master`: PR + 1 review required before merge, applies to Owner role too (including Chris himself — nobody force-pushes master). This enforces the cross-review discipline we already practice informally, at the tooling layer.

### Why the Member/Chris split

Same blast-radius logic Chris applies to his own scope. Chris-role surfaces (billing, org deletion, unilateral membership changes) aren't capabilities you'd need for the 2nd-PGP-contact role or day-to-day contribution. They're also not capabilities Chris uses day-to-day — they're held for the rare cases where they're actually needed (setting up the org, adding branch protection, etc.). If the succession plan ever evolves toward co-primary (Chris plans extended time away, or hands off day-to-day), expanding your scope is a one-line change in GitHub Settings. Today's shape reflects today's work division, not a ceiling.

### [CHRIS] Org invite

```
GitHub web UI:
  InterGenJLU org → People → Invite member
    Enter your GitHub username (Chris confirms with you)
    Role: Member
    Send invite

You accept:
  Notifications → Accept invite
  Enable 2FA if not already (the org-wide requirement below will enforce this)
  Add SSH public key to your GitHub account for git push
```

### [CHRIS] Enable org-wide 2FA requirement

```
GitHub web UI:
  InterGenJLU org → Settings → Authentication security → Require two-factor authentication
```

Applies to every org member (including Chris) — standard hygiene.

### [CHRIS] Branch protection on master

```
GitHub web UI:
  InterGenJLU/intergenos → Settings → Branches → Add branch protection rule
    Branch name pattern: master
    Require a pull request before merging: ON
      Require approvals: 1
    Do not allow bypassing the above settings: ON (applies to Owner role too)
```

Closes the force-push surface for everyone. Enforces the cross-review discipline at the GitHub layer rather than honor system.

---

## Credential summary (what flows where, all Chris↔Ethan direct)

| Credential | Who issues | How delivered | Lives where |
|---|---|---|---|
| VPS login password (= sudo password) | Chris generates | Chris→Ethan direct via Signal or similar | Your password manager |
| Your SSH public key | You generate | Ethan→Chris (plaintext email OK — public key) | VPS `/home/ethan/.ssh/authorized_keys` |
| MCP DCR initial tokens (one per agent_id) | Chris mints per agent_id | Chris→Ethan direct (encrypted zip + out-of-band password) | Consumed on first DCR; your MCP client retains post-DCR client_id + OAuth tokens |
| GitHub org membership | Chris invites via GitHub UI | GitHub email invite | Your GitHub account |
| Your PGP keypair (2nd-contact role) | You generate | Ethan uploads pub half to keys.openpgp.org + shares fingerprint with Chris for doc updates | Your keyring |
| (NOT distributed) Shared direct-bus channel key | N/A | N/A — MCP-only at onboarding | N/A |

None of these land in the Git repo or our agent memory. Flow is Chris↔you direct, with each party retaining their side.

---

## Timeline

**Not urgent.** The first material deadline where your role is first exercised is the shim-review PR target of 2026-05-15 (per the D1-7b timeline main and I worked out 2026-04-19). From now to that date is ~3 weeks.

The submission needs you to have:
- Working access across all four surfaces (VPS + GitHub + MCP + security@)
- A PGP keypair generated, published to keys.openpgp.org with role-UID hardening
- Enough familiarity with `docs/research/installer/signing_key_custody_2026-04-18.md` to stand behind your 2nd-contact listing
- Enough familiarity with `docs/research/installer/ms_shim_sponsorship_2026-04-18.md` to know what the submission is asking

Reasonable internal milestone: productive by 2026-05-01, giving ~2 weeks of runway before the 2026-05-15 shim-review window. Entirely your pace — if life + day-job scheduling push this out, that's workable.

Chris may talk to you this evening (2026-04-20) — if so, the DCR packet pattern described in Surface 3 is ready to send as soon as he greenlights this scaffold commit.
