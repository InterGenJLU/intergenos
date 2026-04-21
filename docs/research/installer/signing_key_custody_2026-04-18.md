# InterGenOS Signing-Key Custody — v2

**Date:** 2026-04-18 (origin) | **Updated:** 2026-04-20 (D1-2 Nitrokey greenlit, 2nd contact named)
**Status:** All D1 decisions resolved. Ready for first-use when signing-key ceremony schedules.
**Authors:** claude-windows (primary), claude-main (D1-8 disclosure framework + cross-review)
**PGP contacts (shim-review + disclosure policy):**
 - Primary: Chris (christopher) — public key + fingerprint to be published at keys.openpgp.org + intergenstudios.com/signing-key (post-ceremony)
 - Secondary: Ethan Bambock (peer-constrained 2nd, confirmed 2026-04-20) — PGP keypair generation + fingerprint registration pending Phase 1 of `docs/succession/ethan_onboarding/03_onboarding_checklist.md`
**Gating for:** Forge SB first-light install (slipped to Tuesday 2026-04-21 per Chris's 2026-04-20 14:18 UTC post; panel delay)

---

## Executive Summary

Two-tier key hierarchy with **three distinct keys** (distro GPG, kernel module X.509 ephemeral, EFI binary sign) + an offline root CA. Chris-approved shape. Monday shim strategy: piggyback on Fedora's pre-signed shim with our vendor cert MOK-enrolled at first boot. Post-Monday: parallel track to obtain our own Microsoft-signed shim via Fedora or SUSE sponsorship.

**Hardware: 2× Nitrokey 3 NFC** (D1-2 resolved 2026-04-20 — Nitrokey greenlit post-Erica conversation; HG Rule #7 open-firmware posture was the tiebreaker). ~$140-150 total.

**Kernel module signing: ephemeral per-build** (confirmed unconditionally correct — see §D1-1 Verification). No persistence needed; kernel's embedded pubkey plus MOK on user side covers both in-tree and DKMS paths.

**Distro GPG + EFI-binary X.509: persistent on the hardware token**, certified by an offline root (Tails-generated, paper + 2× LUKS-USB backup in home + bank safe-deposit).

**Compromise disclosure:** two distinct policies — software-vuln framework (claude-main, 48h ack / 14-30-60-90d fix by severity / 90d max embargo) + trust-anchor policy (claude-windows, immediate ack / 6h revocation / simultaneous disclosure / confirmed by evidence).

---

## Resolved decisions

| # | Question | Decision | Resolved |
|---|---|---|---|
| D1-1 | Kernel module-signing key: hardware-pinned or ephemeral-per-build? | **Ephemeral per-build, unconditional.** Kernel's in-tree pubkey embedding + user-side MOK handles all verification paths. | 2026-04-18 |
| D1-2 | Hardware token: YubiKey 5C NFC vs Nitrokey 3 NFC | **Nitrokey 3 NFC.** HG Rule #7 open-firmware posture tiebreaker. | 2026-04-20 (post-Erica) |
| D1-3 | Root-key physical custody | **Both.** Home safe + bank safety-deposit box. Geo-redundant recovery. | 2026-04-18 |
| D1-4 | Touch-to-sign policy | **Split.** Touch required on release-signing subkey; touch disabled on kernel module-signing slot (hundreds of signatures per kernel build is untenable with touch). | 2026-04-18 |
| D1-5 | Build-host posture | **SPLIT.** Build on igos-build VM (reproducibility + snapshot/rollback). Sign on Chris's workstation directly with token plugged in (no USB passthrough). | 2026-04-18 |
| D1-6 | Publish pubkey to keys.openpgp.org | **Yes.** With hardening: role-based UID only ("InterGenOS Release Key"), no personal email, fingerprint cross-published in repo docs + GitHub releases + intergenstudios.com (TLS). | 2026-04-18 |
| D1-7 | Shim strategy for first-light install | **Option B — piggyback on Fedora's pre-signed shim** with our vendor cert MOK-enrolled at first boot. Option A (our own MS-signed shim) queued as post-first-install parallel track per D1-7b. | 2026-04-18 |
| D1-7b | Option A MS-signed shim process start date | **Target dates confirmed:** advisory email 2026-05-02 / formal shim-review PR 2026-05-15 / MS 2011 CA hard deadline 2026-06-27. | 2026-04-19 |
| D1-8 | Accept combined disclosure policy | **Accepted + committed** as public `SECURITY.md` at commit 92781c3. Chris amendments: 12h trust-anchor revocation, MITRE-direct CVE path, Hall of Fame yes, bug bounty no. | 2026-04-19 |

## 2 PGP contacts (named 2026-04-20)

- **Primary: Chris (christopher).** Role-UID: "InterGenOS Release Key" (personal email not in UID per D1-6 hardening). Fingerprint + pubkey published post-ceremony to keys.openpgp.org + intergenstudios.com/signing-key.
- **Secondary: Ethan Bambock.** Peer-constrained 2nd confirmed 2026-04-20 (see `docs/succession/ethan_onboarding/README.md` for role framing). PGP keypair generation is in Phase 1 of Ethan's onboarding checklist; fingerprint registration will update this doc in a follow-up commit once available.

---

## Context from existing InterGenOS code (as of commit 887599e)

### pkm already has GPG signing infrastructure

`pkm/repo.py` implements the full chain of trust:
- `sign_index(index_path, gpg_key_id=None)` at L438 — creates GPG detached signature via `gpg --detach-sign --armor`
- `_verify_signature(db_path, sig_path)` at L211 — verifies signature on sync
- Chain: GPG key → signed index → SHA256-per-package → package file (documented L41-45)
- `InterGenOS.db.sig` is the committed filename convention

**Implication:** The GPG-signing interface is already wired. The distro GPG signing key is `gpg_key_id` at signing time — will live on the hardware token via PKCS#11 or OpenPGP card protocol.

### Kernel config already has module-signing partially configured

`config/kernel/fragments/00-universal-baseline.config`:
- L642: `CONFIG_MODULE_SIG=y`
- L643: `CONFIG_MODULE_SIG_ALL=y`
- L2573: `CONFIG_SYSTEM_TRUSTED_KEYRING=y`
- L2917-2923: Full `CONFIG_INTEGRITY*` framework

**Forge SB packaging (claude-main's A6) must add to bring to HG compliance:**
- `CONFIG_MODULE_SIG_FORCE=y` — reject unsigned modules at runtime (currently signs but doesn't enforce)
- `CONFIG_SECONDARY_TRUSTED_KEYRING=y` — allow MOK-enrolled keys to chain into module-trust (required for DKMS / NVIDIA on user side)

**What NOT to add:** explicit `CONFIG_MODULE_SIG_KEY=...`. Leaving it unset keeps the kernel's ephemeral-per-build default, which is the Chris-approved D1-1 decision.

### The three-keys reality

D1 manages three keys the distro owns (the fourth — MOK — is end-user owned and out of scope):

| # | Key | Purpose | Custody | Lifetime |
|---|---|---|---|---|
| 1 | Distro GPG | Signs pkm repo index; signs release artifacts | **Hardware token (persistent subkey), certified by offline root** | 2y subkey / 5y root |
| 2 | Kernel module X.509 | Signs in-tree kernel modules at build; pubkey embedded in each kernel image | **Ephemeral per-build, discarded after build** | Per kernel build |
| 3 | EFI-binary X.509 | Signs our custom GRUB build via sbsign; shim trusts via MOK enrollment | **Hardware token (PIV slot 9c), certified by offline root** | 2y subkey / tied to root lifetime |

Not-our-key: **MOK (Machine Chris Key)** — generated per-install by Forge installer on the user's machine. Signs DKMS out-of-tree modules (e.g. NVIDIA). End user owns. Out of scope for D1.

---

## D1-1 Ephemeral-per-build verification

Chris-flagged concern: does ephemeral complicate packaged-install vs build-install logic once we have a mirror repo?

**Verification (cross-confirmed by claude-main 18:50 UTC):**

Linux kernel build flow with `CONFIG_MODULE_SIG=y` + no explicit `CONFIG_MODULE_SIG_KEY`:
1. `make` auto-generates `certs/signing_key.pem` at build time (X.509 keypair).
2. Kernel compilation embeds the **public** half into vmlinuz's built-in trusted keyring.
3. All in-tree `.ko` files are signed with the **private** half.
4. Private half is discarded at end of build.
5. Output: vmlinuz (with embedded pubkey) + signed modules, all keyed to the same ephemeral keypair. **Matched unit.**

**Packaging/mirror/install flow:**
- `pkm` ships `kernel-X.Y.Z.pkg` as one bundle (vmlinuz + modules).
- `pkm/repo.sign_index()` signs the repo INDEX with the persistent distro GPG key.
- User installs kernel package: vmlinuz → `/boot/`, modules → `/lib/modules/X.Y.Z/`.
- On boot: kernel loads, each module's signature verifies against pubkey baked into the same vmlinuz. **No external key lookup.**

**DKMS / out-of-tree path:**
- User's MOK enrolled at first boot (Forge installer generates, MokManager confirms).
- DKMS builds NVIDIA module against kernel headers, signs with user's MOK private half.
- Kernel's `CONFIG_SECONDARY_TRUSTED_KEYRING` (MOK-enrolled) recognizes the signature. Module loads.
- **Our distro signing key plays no role.**

**Conclusion:** D1-1 ephemeral is UNCONDITIONALLY CORRECT.

---

## Options evaluated (final)

Summary tables; full analysis of each in v1 draft (preserved in channel log 17:34:03 UTC).

### Decided

- **Option 1 (VPS secrets vault with restricted SSH):** REJECTED. Networked key violates HG rule #1.
- **Option 2 (Hardware token):** CHOSEN for distro GPG + EFI-binary X.509 keys. Chris picks YubiKey 5C NFC or Nitrokey 3 NFC.
- **Option 3 (Ephemeral per-build):** CHOSEN for kernel module X.509 key only. Rejected for distro-GPG / EFI-binary keys (breaks MOK continuity).
- **Option 4 (Offline air-gapped):** CHOSEN for root CA only. Tails-generated, 2× LUKS-USB + paper backup, stored in home + bank safes.
- **Option 5 (HSM):** DEFERRED to post-v1 upgrade path. Overkill for solo-dev v1; worth revisiting when project has revenue or compliance requirements.

### Comparison matrix (HG-weighted)

Extraction-cost criterion weighted 3× per HG rule #1.

| Criterion | VPS+SSH | **Hardware Token** | Ephemeral | Air-gapped | HSM |
|---|:---:|:---:|:---:|:---:|:---:|
| HG extraction resistance (×3) | 3 | **15** | 15 | 15 | 15 |
| Build-pipeline friction (inv) | 5 | 3 | 4 | 1 | 3 |
| Key rotation story | 4 | 4 | 1 | 3 | 4 |
| Solo-dev ops feasibility | 5 | 4 | 3 | 2 | 2 |
| Upfront cost (inv) | 5 | 4 | 5 | 3 | 1 |
| Recovery from key loss | 4 | 3 | 5 | 3 | 2 |
| Signature continuity | 5 | 5 | 1 | 5 | 5 |
| **HG-weighted total** | 31 | **38** | 34 | 32 | 32 |

---

## Hardware token: Nitrokey 3 NFC (D1-2 resolved 2026-04-20)

Both candidates met HG posture; trade-off summary retained below as decision-record.

| Dimension | YubiKey 5C NFC | Nitrokey 3 NFC |
|---|---|---|
| Firmware | Proprietary, closed | Open-source (Rust), auditable |
| Ecosystem maturity | Very mature, 10+ years | Newer (2022), rougher tooling |
| Protocols | PIV, OpenPGP, FIDO2, OATH, PKCS#11 | Same, plus Passkey storage |
| Firmware update | Not updatable (anti-tamper) | Updatable (two-edged sword) |
| Price | ~$75 | ~$70 |
| HG Rule #7 (open-source responsibility) | less aligned | **more aligned** |
| HG Rule #1 (no convenience trade-offs) | mature tooling helps | marginal cost in rougher tooling |

**Decision: Nitrokey 3 NFC** (Chris-confirmed 2026-04-20, post-Erica conversation; relayed via claude-main channel post 21:14 UTC). HG Rule #7 open-firmware posture was the tiebreaker — if the threat model assumes superhuman adversaries, the hardware whose firmware we can independently verify is the HG-cleaner choice.

Outstanding item: scheduling the signing-key ceremony (brings the Nitrokey order + offline-Tails root CA ceremony forward). Chris pick on timing; no Monday-critical dependency.

---

## Touch-to-sign policy (D1-4 decision)

**Split policy adopted:**
- **Release-signing subkey:** touch REQUIRED. One touch per release. Protects against compromised host silently signing malicious releases.
- **Kernel module-signing key:** N/A — ephemeral per-build never touches the hardware token.
- **EFI-binary signing slot (PIV 9c):** touch REQUIRED for GRUB sign. Signs infrequently (per kernel major version).

**Mitigation for the non-touched paths:** only perform kernel builds on the trusted build host (igos-build VM); keep the build host's attack surface minimal; fresh snapshot before each signing window.

---

## Publishing the public key (D1-6 decision)

**Yes, publish to keys.openpgp.org**, with these hardening rules:
1. Generate distro key with a **role-based UID only**: "InterGenOS Release Key". Zero personal info.
2. Use a role-based email (`release@intergenstudios.com`) or no email — never the Chris's personal address.
3. Cross-publish the fingerprint in **at least three places**:
   - Repo docs (`docs/signing-key.md`, git-tracked)
   - GitHub releases page (pinned)
   - `intergenstudios.com` website (TLS)
4. Sign the fingerprint announcement with the **offline root key** so users can verify the announcement chain independently of the signing subkey.

Once uploaded, treat the fingerprint as permanent — SKS pool mirrors + Google cache + Wayback Machine archive it. GDPR takedown supports UID removal only, not fingerprint erasure.

---

## Shim strategy (D1-7 decision)

**Monday: Option B — piggyback on Fedora's pre-signed shim** with our vendor cert MOK-enrolled at first boot.
- Ships Fedora-upstream `shim.efi` in the Monday installer.
- First-boot MokManager prompt enrolls our vendor cert.
- From then on, shim loads our signed GRUB via the enrolled cert.
- Zero-dollar, Monday-achievable.

**Post-Monday parallel track: Option A — obtain our own Microsoft-signed shim.**
- Fork `rhboot/shim`. Embed our vendor cert.
- Submit via Microsoft UEFI CA sponsorship (Fedora or SUSE).
- 6-12 week turnaround.
- Migration seamless: users' enrolled vendor cert stays the same across the shim swap.
- **Start date — Chris pick pending.** Recommend ordering Fedora/SUSE sponsorship conversation within 2 weeks to kick off the process while Monday Monday SB install stabilizes.

---

## Disclosure policy (D1-8, combined from claude-main + claude-windows)

### Software vulnerability disclosure (claude-main's framework)

**Acknowledgment SLA: 48 hours.** Triage confirmation within 2 business days.

**Fix targets by severity (from triage time):**
| Severity | Fix target |
|---|---|
| CRITICAL (RCE, auth bypass, Secure Boot chain break) | 14 days |
| HIGH (local privesc, crypto weakness) | 30 days |
| MEDIUM (DoS, info disclosure) | 60 days |
| LOW (defense-in-depth gaps) | 90 days or next release |

**Public disclosure:** at fix release OR 90 days from acknowledgment, whichever comes first (Project-Zero-style safety net).

**Upstream coordination:** if the vuln is in upstream code (kernel, systemd, glibc), we coordinate with their embargo (up to 30-60 days) — but our own users get the fix when upstream does, not later.

**Reporting channel:**
- Primary: `security@intergenstudios.com` (pending VPS mail setup)
- Alternative: GitHub private security advisory on `InterGenJLU/intergenos`
- Anonymous reports accepted

**Reporter credit:** always, unless anonymity requested.

**Advisory format:** CVE (via CNA or MITRE assignment), CVSS score, affected versions, mitigation, patch commit, timeline, reporter credit.

### Trust-anchor compromise policy (claude-windows addition)

Distinct from software-vuln: a signing-key compromise is a break in the TRUST ANCHOR, not a patch-able bug. Standard SLA framework does not apply.

**Confirmed compromise definition** (claude-main refinement):
- (a) evidence of private-key material exposure (exfiltration artifact, device tampering indicator, or credential leak), OR
- (b) anomalous signature observed in the wild that we did not authorize.

Both require **evidence, not just a claim**. False reports route to standard 48h triage and likely close without action.

**Response on confirmed compromise:**
- **Acknowledgment: immediate.** The moment we confirm, we act.
- **Revocation + new keyring package: within 6 hours.** Target: users running `pkm update` within 24h of incident receive the new keyring and revoke trust in the old key.
- **Public disclosure: simultaneous with revocation publication.** No embargo. Users must know their trust anchor was broken.
- **Advisory content:** fingerprint of compromised key, first known compromised signature timestamp (if known), any downstream artifacts suspected tampered, replacement key fingerprint, verification instructions, incident timeline.
- **Rollover mechanics:** new keyring signed by the offline ROOT key. This is why the root is air-gapped — subkey compromise is survivable only if root is still trustworthy.

**HG reasoning:**
- Rule #1: convenience is never a reason to delay trust-anchor revocation.
- Rule #9: update infrastructure must be trustworthy; compromised signing key makes the update path itself the attack.
- Rule #10: if there's any doubt about key integrity, revoke. Better to re-issue 10x unnecessarily than leave users exposed once.

---

## Signing-ceremony checklist (all D1 decisions resolved)

1. **Hardware order:** 2× Nitrokey 3 NFC (~$140-150 total). Chris schedules when convenient; not Monday-critical since first-light install (slipped to Tuesday 2026-04-21) uses Fedora's pre-signed shim (D1-7) and per-user MOK signing, neither of which needs the offline-root ceremony complete.
2. **Tails USB preparation:** WiFi rfkill-blocked, ethernet unplugged. This is the root-keygen environment.
3. **Root key ceremony:**
   - `gpg --full-generate-key`, RSA 4096 or Ed25519, no expiry on primary, 2-year expiry on subkeys.
   - UID: `"InterGenOS Release Key"` with role-based email or no email.
   - Export revocation cert immediately.
4. **Subkeys onto hardware token:**
   - `[S]` subkey via `gpg --edit-key` → `addkey` → move to card.
   - Generate separate PKCS#11 X.509 keypair in PIV slot 9c for EFI-binary signing (sbsign).
   - Touch required on [S]; touch required on 9c.
5. **Root backup:**
   - `paperkey` printout (home safe).
   - 2× LUKS-encrypted USBs: one in home safe, one in bank safety-deposit box.
   - Destroy Tails session.
6. **Publish public key:**
   - Ship `intergenos-keyring` package (public key + signed-by-root self-attestation).
   - Upload to `keys.openpgp.org`.
   - Cross-publish fingerprint in `docs/signing-key.md`, GitHub release notes, `intergenstudios.com`.
7. **Wire into build pipeline (split build/sign per D1-5):**
   - BUILD STEP runs in igos-build VM: unsigned artifacts produced here. Kernel build leaves `CONFIG_MODULE_SIG_KEY` unset (ephemeral default); add `CONFIG_MODULE_SIG_FORCE=y` and `CONFIG_SECONDARY_TRUSTED_KEYRING=y` to the fragment. Module signing happens inside the kernel build (ephemeral, never touches the token).
   - HANDOFF: VM writes unsigned artifacts to a shared location (virtiofs `/mnt/intergenos`, or explicit scp to workstation staging dir). Manifest file enumerates what needs signing.
   - SIGN STEP runs on Chris's workstation (no VM passthrough):
     * `scripts/sign-release.sh` (NEW) — invokes `gpg --detach-sign` for pkm repo index; invokes `sbsign` with PKCS#11 URI for GRUB; signs kernel vmlinuz with distro EFI key.
     * Workstation must have token plugged in + PIN unlocked. Touch required on release-signing subkey; touch required on PIV slot 9c (EFI sign). Signing loops are bounded (not module-signing hundreds of times — that's in-VM).
     * Build fails hard if token not present when sign-release.sh runs.
     * **Signing-window discipline** (claude-main addition 19:12 UTC): treat each signing session as a key ceremony. Close browsers, untrusted dev tools, and non-essential background processes before running `sign-release.sh`. Touch-to-sign prevents a compromised workstation from silently producing signatures, but minimizing concurrent attack surface during the signing window is defense-in-depth. Document as a short pre-sign checklist in `docs/signing-procedure.md`.
   - SIGNED ARTIFACTS return to the pipeline via the same handoff channel (signed.tar.xz back to VM's shared location, or scp to build-output dir).
8. **Document procedure:**
   - `docs/signing-procedure.md`: step-by-step operational runbook.
   - `docs/signing-key.md`: fingerprint publication + verification instructions.
   - `docs/security-policy.md`: disclosure policies (software-vuln + trust-anchor compromise).
9. **Shim integration (Forge):**
   - Bundle Fedora's `shim.efi` in Monday installer.
   - Install-time flow: MOK enrollment prompt → MokManager first-boot → our vendor cert enrolled.
10. **Test harness coordination** (claude-laptop):
    - Class 1 signed-chain verification runs against canonical-key artifacts.
    - Test runs use ephemeral test keys — never touch prod key material.

---

## Forge SB packaging impact (for claude-main A/B)

Updated from v1 with final decisions:

- **A6 kernel package update:** add `CONFIG_MODULE_SIG_FORCE=y` + `CONFIG_SECONDARY_TRUSTED_KEYRING=y`. **Do NOT** set `CONFIG_MODULE_SIG_KEY` — preserve the kernel's ephemeral-per-build default.
- **B1 bootloader.py rewrite:** install Fedora shim to ESP; install our signed GRUB (sbsign via PKCS#11 URI pointing at PIV slot 9c); register shim as primary UEFI entry via efibootmgr.
- **B2 mok.py (new):** generate_mok_keypair(), queue_mok_enrollment() via mokutil --import, vendor-cert import routine. Unchanged in scope from v1 except the vendor cert now chains up to our Forge-enrolled MOK root, not a shim-signed root.
- **B3 config.py:** ESP mount adds umask=0077 (unchanged from v1).
- **B4 frontend/tui.py:** new MOK setup screen + first-boot MokManager instructions. Also: explainer that DKMS will use THEIR MOK, not our distro key.
- **B5 backend/disks.py:** alongside-shrink branch conditional on Q5 — see Monday scenario.
- **New: scripts/sign-release.sh:** invoked by master build orchestrator (`build-intergenos.sh`) between the final package-build phase and the image-creation phase. Calls `gpg --detach-sign` for `pkm/InterGenOS.db.sig`, `sbsign` for kernel + GRUB.

---

## Post-Monday parallel tracks

- **MS-signed shim (Option A):** start Fedora/SUSE sponsorship conversation within 2 weeks. Target Microsoft signing submission within 4 weeks. Expected sign turnaround: 6-12 weeks. Migration seamless for users (vendor cert unchanged).
- **HSM upgrade path:** if project grows to need FIPS-level assurance (paid infrastructure, regulated customers), YubiHSM2 on-prem (~$650) or CloudHSM (if cloud-trust-dependency becomes acceptable) are upgrade targets. Not v1 — not v2 — not until there's a business case.
- **Mirror network:** if we start shipping to mirrors, repo-index-sign cadence becomes the bottleneck. Automate sign-release.sh via touch-required sessions (acceptable pain), not automated signing (not acceptable).
- **Post-quantum migration path:** PQC hash/sig algorithms for module sig when kernel supports. Track upstream.

---

## Known gaps

- **Hardware supply-chain trust:** neither YubiKey nor Nitrokey has perfect supply-chain integrity. Residual risk. Nitrokey's open firmware reduces but doesn't eliminate. No better option exists at this scale.
- **Revocation distribution:** if the token is lost and revocation is issued, users learn via `intergenos-keyring` package update. This depends on users having working network + DNS trust. Mitigation: shim includes a static fallback trust list signed by root.
- **Token theft during travel:** both primary and backup in secure storage. If primary is with Chris during travel and stolen, revoke from backup, generate new subkey, publish.
- **`CONFIG_SYSTEM_TRUSTED_KEYS`:** if we ever want to pin additional long-lived trust anchors into the kernel image directly, this is the Kconfig knob. Not needed for the ephemeral-module + MOK-for-DKMS architecture, but option exists if design changes.

---

## Sources

**Prior art (research survey, 17:28-17:33 UTC):**
- Debian key-rollover: https://www.debian.org/security/key-rollover/
- Fedora sigul: https://pagure.io/sigul
- Arch packager master keys
- Ubuntu launchpad-signing-service
- NixOS binary cache signing
- Alpine abuild-keygen

**Local code audit:**
- `pkm/repo.py` L7, L41-45, L178-180, L211, L307-330, L438-456
- `config/kernel/fragments/00-universal-baseline.config` L642-643, L2573, L2917-2923
- `scripts/build-intergenos.sh` (no current signing step — sign-release.sh will be added)

**Prior Forge SB thread artifacts:**
- 2026-04-18 14:53:15 UTC: claude-windows Forge SB scope A/B/C/D split
- 2026-04-18 16:37:51 UTC: claude-laptop permissions-squash v1 + scope clarification (HG governs product, not dev tools)
- 2026-04-18 17:34:03 UTC: D1 draft v1 posted
- 2026-04-18 18:20:37 UTC: Chris-decisions Q3/Q5/Q6/Q7/D1-1-8 packet
- 2026-04-18 18:24:04 + 18:40:44 UTC: claude-windows ELI5 batches 1+2
- 2026-04-18 18:26:35 UTC: claude-laptop test-harness v1 scope
- 2026-04-18 18:36:56 UTC: claude-main D1-8 disclosure framework proposal
- 2026-04-18 18:47:37 UTC: claude-windows Q6 math + D1-1 verification + D1-8 trust-anchor addition
- 2026-04-18 18:50:31 UTC: claude-main D1 cross-review ack + confirmed-compromise criteria
