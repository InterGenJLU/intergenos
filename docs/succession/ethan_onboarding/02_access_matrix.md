# Access Matrix — Surface by Surface

**Written by:** claude-windows, 2026-04-20 (v2 2026-04-21 — extended symmetric framing throughout)

Each row below is either (a) a capability you'd have and use day-one, or (b) a surface scoped to Chris because of blast-radius or infrastructure-cost reasons that would apply to anyone on that side of the split. Access limits are mutual safety nets — the same logic Chris applies to his own scope, applied symmetrically to yours. Not permission gates.

---

## Surface-by-Surface

| Surface | Level | Scope | Pattern mirrors | Decided |
|---|---|---|---|---|
| **VPS shell** | Wheel-group sudo | Full sudo equivalence via your own login password — functionally equivalent to christopher's sudo | `christopher` | Wheel-sudo parity |
| **VPS SSH** | Port 2200, pubkey auth | Your SSH public key in `~/.ssh/authorized_keys` | christopher | Standard pubkey-only |
| **Coordination channel (direct-bus)** | Not distributed at onboarding | N/A — MCP below covers same capabilities per-agent | N/A | MCP-only initially; revisit if your automation later needs it |
| **MCP wrapper** | OAuth 2.1 with URL-scoped agent identity | Per-agent URLs at `/mcp/{agent_id}/`; DCR initial token minted per agent_id | Per main's 2026-04-20 MCP v2 deploy | Primary onboarding path |
| **GitHub InterGenJLU org** | Member role | Write to repo (push, PR, merge); cannot manage org settings/billing/membership | N/A (you're the 2nd org member) | Member + branch protection on master required (applies to Chris too) |
| **GitHub 2FA** | Required (org-enforced) | Must enable to accept invite | Chris's own account | Enforced org-wide |
| **security@intergenstudios.com** | Shared mailbox access | Sees every vulnerability disclosure reported per SECURITY.md | Chris's own mailbox | Shared (Option A) — in real-time, alongside Chris |
| **Signing-key custody role** | 2nd PGP contact | Per `signing_key_custody_2026-04-18.md`; doc updated in same commit as this scaffold to name you + your fingerprint | N/A (new surface) | Confirmed; doc edits bundled with this scaffold |

---

## How Chris's Scope Compares

Chris has, on his side:
- VPS `christopher` user + wheel-sudo (same pattern you'd get)
- GitHub org Owner role (org management + billing surfaces)
- Direct cPanel access to intergenstudios.com (infrastructure/billing surface)
- MCP tokens for pre-OAuth legacy agents (`ubuntu-claude`, `intergenos-claude`, `windows-claude`, `iphone-claude`) + newer OAuth-scoped agents (`chris-ubuntu-web-claude`, `chris-ios-app-claude`, etc.)
- security@ mailbox as primary recipient

Your defaults would give you:
- VPS user with wheel-group sudo (same as christopher)
- GitHub Member — full day-to-day co-contribution coverage, not org administration
- MCP tokens for your own agent_ids via DCR flow (same OAuth path Chris uses today, URL-scoped + fingerprint-bound per agent_id)
- security@ mailbox shared access (Option A)
- 2nd PGP contact role per D1 signing-key custody doc

The differences are about blast radius + infrastructure-cost, not trust:
- **GitHub Owner role** — Chris-held for the same reason single-operator tooling is single-operator: blast radius from billing/deletion/membership changes would affect the whole org. When succession plan evolves toward co-primary, this expands.
- **cPanel** — infrastructure/billing surface; Chris-exclusive for cost-center separation from project-technical work. Same logic would apply in reverse — your own hosting/infrastructure costs aren't something that'd flow into our fleet.
- **Legacy direct-bus key + pre-OAuth MCP tokens** — historical credentials from Chris's pre-MCP-v2 single-operator period. Not re-issued for anyone new; new agents onboard via OAuth regardless of who operates them.

---

## security@intergenstudios.com — Option A applied

Shared mailbox access. Chris adds you as alias/co-recipient on the `security@` forwarding list; you see disclosures in real-time alongside Chris. No reporting bottleneck.

Chris keeps the right to reassign the routing later (to a dedicated ethan@ address, or Option B internal-routing) if your day-job Comerica role develops any conflict-of-interest considerations around direct receipt of security@ traffic. That's a personnel decision — Chris owns it, technical default is shared-access today.

---

## Signing-Key Custody Role

You're the 2nd PGP contact on shim-review and on our public SECURITY.md disclosure policy. The two docs that reference contact identity are updated in the same commit as this scaffold (Chris's 2026-04-20 decision #7):

1. `docs/research/installer/signing_key_custody_2026-04-18.md`
   - Names you as 2nd contact
   - Fingerprint field populated once you've generated your PGP keypair + shared it with Chris (follow-up edit after Phase 1 of the onboarding checklist)
   - D1-2 Nitrokey decision captured (Nitrokey 3 NFC greenlit 2026-04-20, post-Erica conversation)

2. `docs/research/installer/ms_shim_sponsorship_2026-04-18.md`
   - Names you as 2nd contact
   - Your preferred email address captured once confirmed

Your PGP keypair generation is in `03_onboarding_checklist.md` Phase 1 — our suggested sequence, your pace.

---

## Surfaces Outside Today's Shape (Mutual Safety Nets)

Not "permissions you lack." Scope decisions that preserve reversibility — same logic applies symmetrically to Chris's side:

- **GitHub org Owner role** — blast radius includes org deletion, billing, unilateral membership changes. Member covers full day-to-day co-contribution; Chris-only surfaces held until succession plan calls for co-primary.
- **cPanel login** — billing and infrastructure-cost surface; Chris-exclusive for cost-center separation, same way your own cost-centers stay on your side.
- **`c-vps-pe.txt`-equivalent password files on our agent machines** — each agent's local automation credential, not a shared pool. When your agents need equivalent automation for `sudo` on the VPS, you'd maintain your own per-machine credential; not synchronized across humans in either direction.
- **Our individual SSH private keys** — each of us generates our own keypair. You generate yours; we keep ours. Symmetric hygiene.
- **Root-SSH on VPS** — disabled for everyone per `sshd_config`. Not specific to you; shared infrastructure policy.
- **coordination.php server-side edits** — cPanel-managed file on Chris's account; the infrastructure-owner's surface regardless of who contributes project-technical work.
- **Shared direct-bus channel key (for now)** — single rotation point affecting the entire fleet. Held with the operator whose fleet it already governs. MCP gives your agents the same channel capabilities without coupling the rotation lifecycle across humans.

If the succession plan evolves toward co-primary, the matrix expands. Today's shape reflects today's work-division, not a ceiling.
