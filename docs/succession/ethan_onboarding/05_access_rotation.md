# How We Rotate Access When It Needs to Change

**Written by:** claude-windows, 2026-04-20 (v2 2026-04-21 — tone pass)

These are our rotation procedures — applicable to any credential, for any operator on the fleet, in any scenario where the credential needs to change. Your credentials, our credentials, same mechanics.

Access rotation is HG Rule #8 hygiene (credential handling is sacred). Procedures below assume either a routine rotation (preventive, hygiene-cadence) or a compromise-triggered rotation (reactive). Both follow the same mechanics; only urgency differs.

---

## Scenarios that trigger rotation

| Scenario | Action scope | Urgency |
|---|---|---|
| Ethan's machine lost/stolen (SSH private key exposed) | VPS pubkey remove + re-issue | IMMEDIATE |
| Ethan's login password exposed (e.g., phishing, clipboard leak) | VPS password reset | IMMEDIATE |
| Shared coordination channel key exposed (Chris-side issue; Ethan doesn't hold this key at onboarding) | Full fleet key rotation | IMMEDIATE |
| Ethan's MCP access token or refresh token exposed | Per-agent-client OAuth re-auth (revoke + new DCR) | IMMEDIATE |
| GitHub account compromise | GitHub session revocation + 2FA reset | IMMEDIATE |
| Routine annual rotation (hygiene) | All of the above, coordinated | PLANNED (weeks) |
| Succession plan changes (role scope expands/contracts or Ethan rotates off) | Scoped-to-change-type | PLANNED (hours-days) |

---

## Procedure 1 — Rotate Ethan's SSH key (VPS access)

**Trigger:** stolen machine, rotation cadence, or suspicion of key exposure.

### [CHRIS — or christopher's agents if Chris is unavailable]

```bash
# On VPS:
ssh -p 2200 christopher@origin.intergenstudios.com
sudo -S bash
vi /home/ethan/.ssh/authorized_keys
# Remove the old ssh-ed25519 line; save
# Add the new ssh-ed25519 line (new pubkey Ethan generated); save
exit
```

### [YOUR SIDE]

Generate new keypair and deliver pubkey to Chris:
```bash
ssh-keygen -t ed25519 -C "ethan@<machine>-<YYYYMM>" -f ~/.ssh/intergenos_vps_ed25519_<YYYYMM>
cat ~/.ssh/intergenos_vps_ed25519_<YYYYMM>.pub
```

Delete the old private key if compromised:
```bash
rm ~/.ssh/intergenos_vps_ed25519   # old key
```

---

## Procedure 2 — Rotate Ethan's VPS login password

**Trigger:** password exposed, rotation cadence.

### [CHRIS]

Chris generates a new strong random password:
```bash
openssl rand -base64 24
```

Distribute Chris→Ethan direct (Signal/secure channel).

Then set it on VPS:
```bash
ssh -p 2200 christopher@origin.intergenstudios.com
sudo -S passwd ethan
# Enter the new password twice at the prompts
```

Ethan's next sudo operation will prompt for the new password.

---

## Procedure 3 — Rotate Shared Coordination Channel Key (Chris-side, fleet-wide)

**Trigger:** key exposed anywhere, or suspicion thereof.

**Ethan doesn't hold this key at onboarding** (per Chris's 2026-04-20 decision #5 — MCP-only initially). This procedure governs Chris's own pre-OAuth fleet (claude-main, claude-laptop, claude-windows) and any future expansion of Ethan's access if he later opts into direct-bus. Documented here for completeness.

This is the biggest-hammer rotation because it affects every legacy agent. Coordinate before executing.

### [CHRIS]

1. Generate new key:
   ```bash
   openssl rand -hex 32
   ```

2. Post a coordination-channel PAUSE notice using the current (soon-old) key:
   ```bash
   curl -s -X POST "https://intergenstudios.com/intergenos/coordination.php" \
     -d "key=<OLD-KEY>" \
     -d "agent=Chris" \
     -d "status=pause-all-agents" \
     -d "task=channel-key-rotation" \
     --data-urlencode "notes=Channel key rotating in ~10 min. All direct-bus agents pause POSTs. New key coming Chris-direct."
   ```

3. SSH to VPS and update the key constant in `coordination.php`:
   ```bash
   ssh -p 2200 christopher@origin.intergenstudios.com
   sudo -S vi /home/intergen/public_html/intergenos/coordination.php
   # Update the AUTH_KEY constant
   ```

4. Distribute new key Chris→each human holding direct-bus access (today just Chris's own machines; Ethan only if he's later enrolled on direct-bus).

5. Each direct-bus agent updates its own `memory/reference_coordination_channel.md` with the new key.

6. Post fleet-resume notice on the channel with the new key.

### Emergency variant (key already in public exposure)

Skip the 10-min pause — it's too late. Immediately update the key on VPS, then distribute. Agents will see `401 unauthorized` on their next POST and surface the issue.

**Note:** MCP-path agents (including all of Ethan's at onboarding) are unaffected by this rotation — MCP uses its own OAuth tokens, separate lifecycle.

---

## Procedure 4 — Rotate Ethan's MCP Access (per-agent, OAuth-based)

**Trigger:** single-agent token exposure; doesn't affect other agents.

### [CHRIS]

1. Revoke the existing OAuth client for that specific `agent_id` (surgical):
   ```bash
   ssh -p 2200 christopher@origin.intergenstudios.com
   # Edit /etc/intergencomms/oauth_state.json — remove the client_id entry matching the compromised agent_id
   # (Main's MCP wrapper has atomic-write on state file per 17:34Z deploy.)
   sudo systemctl reload intergen-mcp.service   # or restart if reload not supported
   ```

2. Mint a replacement DCR initial token for that same `agent_id`:
   ```bash
   sudo /opt/intergen-mcp/mint-dcr-token.py --description "Replacement for Ethan's <client> as <agent_id> (rotation YYYY-MM-DD)" --ttl-days 30
   ```

3. Distribute the new DCR bundle Chris→Ethan direct (encrypted zip + out-of-band password).

4. Ethan re-configures that one client with the new bundle (DCR → authorize → new access token). Other agent_ids Ethan holds are untouched.

Single-agent rotation is **scoped** — other agents' OAuth tokens (Chris's, laptop's, other Ethan agent_ids) continue to work.

**Legacy Bearer token rotation (for the pre-OAuth fleet)** follows a different pattern — edit `/etc/intergencomms/tokens.json` directly and restart. Only applies to the 4 legacy agents (`ubuntu-claude`, `intergenos-claude`, `windows-claude`, `iphone-claude`); not used for Ethan's onboarding.

---

## Procedure 5 — GitHub Access Revocation

**Trigger:** account compromise, project departure.

### [CHRIS]

Org-level:
```
GitHub web UI:
  InterGenJLU org → People → Ethan's entry → Remove from organization
```

Additionally, if GitHub account compromise (not just departure):
- Chris should audit recent repo activity (Insights → Contributors or git log on master) for any unexpected pushes
- Consider re-signing any git tags or re-verifying commit signatures from affected window
- Report to GitHub if credential compromise was their auth flow failing

### [YOUR SIDE — on succession-plan change]

If the succession plan evolves and your 2nd-contact role winds down:

- Delete the local repo clone (or keep read-only; no longer pushing)
- Remove VPS SSH private key from your keychain
- Delete MCP tokens from your AI-assistant clients (each client manages its own OAuth artifacts — Claude, GPT-5, Gemini, or whichever you've been using)
- (Direct-bus channel key does not apply — wasn't distributed per onboarding shape)

---

## Procedure 6 — Full Access Teardown (succession-plan change or security incident)

Coordinated sequence, hours-to-days timeline. Run all of the above:

1. **GitHub** — remove from org
2. **VPS** — remove his `authorized_keys` entry; lock or delete `ethan` user (Chris's decision — if role may resume, lock; if clean exit, delete)
3. **MCP** — delete his OAuth client entries from `oauth_state.json`, restart `intergen-mcp.service`
4. **Coordination channel direct-bus key** — NOT rotated (Ethan doesn't hold it per onboarding decision #5). If he later had been enrolled on direct-bus, add key rotation per Procedure 3.
5. **security@** — remove him from the shared mailbox forwarding list
6. **Docs** — update `signing_key_custody_*.md` and `ms_shim_sponsorship_*.md` to name the new 2nd contact (or mark the role as re-opening)
7. **Memory** — all agents update `reference_coordination_channel.md` to remove his agent_ids; update `MEMORY.md` / `project_succession_plan.md` to reflect the succession change

Chris should consider leaving a short note in a `memory/project_succession_history.md` (new file) summarizing the transition for future reference.

---

## Cadence Recommendations

| Surface | Routine rotation cadence | Rationale |
|---|---|---|
| SSH keys (everyone) | Every 2 years | ED25519 no practical crypto wear; rotation is hygiene, not mandate |
| VPS login passwords | Every 1 year | Balances "hard to compromise fresh" vs "memorability" |
| Coordination channel key | Every 2 years OR on fleet-size change | Rotating this is expensive (fleet-wide); do it only when meaningful |
| MCP tokens | Every 1 year OR per-event | Lower cost to rotate (single-token scope); more frequent is cheap |
| GitHub 2FA recovery codes | Every 1 year | Personal hygiene, not project-level |
| PGP signing keys | Every 2 years with 3-month expiry overlap | Matches shim-review's typical contact rotation cadence |

---

## What's NOT rotated on a cadence

- **The Chris's own root password on VPS** — that's Chris's personal credential hygiene; not a project-level rotation
- **cPanel login** — Chris's billing-surface credential; Chris's personal responsibility
- **GitHub Chris account** — Chris's personal login; not subject to project-level rotation policy

---

## Post-Rotation Verification Checklist

After any rotation, verify:

- [ ] The old credential no longer works (attempt the operation, confirm 401/denied)
- [ ] The new credential works for the intended actor (smoke-test one operation)
- [ ] Channel post announcing rotation completed (so other agents see the rotation happened)
- [ ] Memory files updated on all affected agents
- [ ] No credentials landed in the Git repo or agent memory in the process

---

## Reference files

- `memory/feedback_holy_grail_scope_product_vs_dev.md` — why credential rotation is governed by HG Rule #8
- `docs/research/installer/signing_key_custody_2026-04-18.md` — signing-key-specific rotation (trust-anchor revocation etc.) is more detailed there
- `SECURITY.md` — public disclosure policy; includes trust-anchor compromise language
