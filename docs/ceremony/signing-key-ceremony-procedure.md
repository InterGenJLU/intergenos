# InterGenOS Signing-Key Ceremony — Procedure

**Last updated:** 2026-05-05 (post-ceremony rewrite)
**Applies to:** the one-time generation of the InterGenOS distro release-signing keys (PGP master + four signing subkeys + EFI X.509 PIV slot 9c vendor cert).
**Status:** completed 2026-05-05. Master fingerprint `5597A3E0587B253006D0DD7B8C50826182083050`.
**Operator role:** primary maintainer present at the air-gapped host; the workstation operator on call for cross-checks.
**Environment:** Tails 7.7 on the air-gapped HP laptop, network-off, four Nitrokey 3 NFC tokens present.

This document describes the methodology and trust posture of the ceremony. It is reviewer-facing — what was generated, why, where it lives, and how the integrity of the procedure is established. The day-of operational detail is owned by the automation (`ceremony.py`, `validate.py`) and the private maintainer runbook; pointers below.

For design rationale: [`docs/research/installer/signing_key_custody_2026-04-18.md`](../research/installer/signing_key_custody_2026-04-18.md).
For the post-ceremony release-signing runbook: [`docs/signing-procedure.md`](../signing-procedure.md).
For the cross-published fingerprint page: [`docs/signing-key.md`](../signing-key.md).
For the post-ceremony lessons-learned: [`docs/research/ceremony/lessons-learned-2026-05-05.md`](../research/ceremony/lessons-learned-2026-05-05.md).

---

## Table of Contents

1. [Pre-ceremony checklist](#part-1--pre-ceremony-checklist)
2. [Execution: automated](#part-2--execution-automated)
3. [Post-ceremony (online side)](#part-3--post-ceremony-online-side)
4. [Recovery branches and known failure modes](#part-4--recovery-branches-and-known-failure-modes)
5. [Glossary and cross-references](#part-5--glossary-and-cross-references)

---

## Part 1 — Pre-ceremony checklist

This list runs once before Tails boots. Every item is either ready or surfaced to the workstation operator for resolution before the air-gap window opens.

### 1.1 Hardware kit

- **4 × USB drives**, sharpie-labeled #1 / #2 / #3 / #4:
  - Drive #1 = Tails 7.7 boot media (sha256 round-trip verified)
  - Drive #2 = OFFLINEDEBS — offline package mirror plus the `scripts/` automation directory (`ceremony.py`, `validate.py`, `bootstrap.sh`, helpers)
  - Drive #3 = CEREMONY (FAT32 label `CEREMONY`) — receives the public output artifacts (`intergenos-release-key.asc`, `intergenos-vendor-cert.pem`) and the LUKS-encrypted master backup
  - Drive #4 = sealed cold-spare; opened only on catastrophic mid-ceremony failure
- **4 × Nitrokey 3 NFC tokens**, labeled by physical custody:
  - NK#1 = primary maintainer's daily-use token (post-ceremony)
  - NK#2 = off-site bank safe deposit box backup
  - NK#3 = secondary maintainer (Ethan Bambock) primary-use token
  - NK#4 = secondary maintainer's hardened-storage backup (fireproof safe)
- **Paper for paperkey backup.** Plain printer paper.
- **Pen.** New PINs are written on paper at the time they are set; never stored electronically.
- **Printer** connected to the workstation (NOT the air-gapped laptop). Paperkey output is plain text.
- **AC power** to the laptop throughout. Battery is power-blip backup only.
- **Workstation phone or browser** with this document available as offline reference. Cross-check channel open to the workstation operator.

### 1.2 Software pre-flight

- Tails 7.7 image sha256 + PGP verified against the canonical Tails master fingerprint.
- Drive #1 imaged with Tails; sha256 round-trip on first 2,041,577,472 bytes verified bit-perfect.
- Drive #2 staged with offline-debs and the automation scripts. Both `/debian12/` and `/debian13/` package set SHA256SUMS verified on-disk.
- Drive #3 freshly formatted FAT32 with label `CEREMONY`.
- Phase 1 host evaluation: PASS verdict from the workstation operator before Phase 2 boot.

### 1.3 Air-gap environment pre-flight

- Bluetooth radio off on the workstation (firmware-level if possible). Air-gapped laptop Bluetooth hard-blocked once Tails is up.
- No USB devices on the air-gapped laptop except those required by the current step.
- Air-gapped laptop is never on the network during the ceremony, regardless of physical connectivity state. WiFi hard-blocked, Ethernet unplugged.
- The workstation operator is reachable for cross-checks. Each stage's expected outputs are designed to be operator-readable for real-time confirmation.

### 1.4 What is generated

| Key | Purpose | Custody | Lifetime |
|---|---|---|---|
| Distro GPG master keypair (RSA-4096) | Certifies signing subkeys; signs rollover announcements | Tails RAM during ceremony; backed up to paperkey × 2 + LUKS USB; never on a network-connected machine | 5 years |
| GPG signing subkeys [S1] [S2] [S3] [S4] (RSA-4096) | Sign the pkm repo index and release artifacts | One subkey per Nitrokey OpenPGP applet (#1, #2, #3, #4); UIF touch-policy on; touch-required for every signing operation | 2 years (next rotation 2028-05-04) |
| GPG encryption subkey [E] (RSA-4096) | Decrypts PGP-encrypted security reports per `SECURITY.md` | Stays on disk inside LUKS backup; not keytocard'd | 2 years |
| EFI-binary X.509 vendor cert (RSA-2048) | Signs `vmlinuz-*-intergenos` and `grubx64.efi` via `sbsign` | Generated on Nitrokey #1's PIV applet, slot 9c; private half never leaves the card; PIV management key rotated to fresh AES-256 | 2 years |

Out of scope for this ceremony:
- **Kernel module-signing key** — ephemeral per-build, generated and discarded inside the kernel build. Independent of the keys above.
- **Machine Owner Key (MOK)** — generated per-install on the end-user machine by the Forge installer. Not distro-held.

### 1.5 Trust chain context

The keys generated today are the trust anchors for InterGenOS's release-signing posture. They sit at the top of an end-to-end attestable chain:

1. **Ceremony output** — distro GPG master + signing subkeys ([S1]/[S2]/[S3]/[S4] on Nitrokeys #1-#4) + EFI X.509 vendor cert (Nitrokey #1, PIV slot 9c). All private material is hardware-bound or paperkey-only; the master never persists on a network-connected machine. This is the hardware-rooted-key custody policy referenced in `docs/research/installer/signing_key_custody_2026-04-18.md`.

2. **Distribution-time signatures** — at every release, an active signing subkey signs the pkm repository index (`InterGenOS.db`); PIV slot 9c signs each `vmlinuz-*-intergenos` and `grubx64.efi`. Both kinds of signature are touch-required on the physical Nitrokey.

3. **Per-file content-hash attestation** — the pkm repository index records per-file SHA-256 for every artifact in every package. Because the index is GPG-signed, its content-hash claims are themselves transitively attested. (Mechanism: pkm + igos-build supersedes primitive + content-hash manifest, landed at master commit `c9534f7`.)

4. **Install-time and on-demand verification** — `pkm verify --strict <package>` re-validates each installed file against its recorded hash. Tampering with an on-disk file is detectable without re-downloading from the repo.

End-to-end: a user holding the published master fingerprint can verify the repo-index signature, walk to any package's per-file SHA-256, and confirm any file on disk against an attestation chain rooted in the ceremony keys.

The ephemeral per-build kernel-module signing key (out of scope, see Part 1.4) and end-user MOK enrollments (per-install, not distro-held) are orthogonal layers above this chain.

---

## Part 2 — Execution: automated

The day-of ceremony is driven by automation, not by hand-typed commands. The choice was deliberate: PGP card-management commands have a high fat-finger cost (irreversible `keytocard` to wrong slot, accidental `factory-reset`, malformed `gpg --edit-key` sequences), and the air-gapped Tails environment offers no easy rollback.

### 2.1 Automation artifact

The driver is `ceremony.py` (~2,150 lines), accompanied by `validate.py` (the ship gate) and `bootstrap.sh` (entry point). All live at:

```
~/intergenos/research/ceremony/c6-offlinedebs/scripts/
```

(home-drive on the maintainer's workstation; copied to Drive #2's `OFFLINEDEBS/scripts/` for the air-gapped run).

`ceremony.py` is structured as a sequence of named stages, each idempotent and resumable via `--from-stage N`:

| Stage | Purpose |
|---|---|
| `pre_flight` | Verify Tails environment, validate input files, capture pre-state |
| `install_debs` | Install offline-debs (smartcard tools + Python deps) into the Tails session |
| `collect_secrets` | Prompt for new PINs, master passphrase, LUKS passphrase; record to RAM-only `values.txt` |
| `phase_0_one_card` (×4) | Per-NK preamble: `revive_pcscd` → factory state → set new PINs → optional PIV dry-run |
| `master_keypair` | Generate RSA-4096 master + encryption subkey in Tails RAM |
| `paperkey` | Print master secret as base16 paperkey (×2 copies) |
| `luks_backup` | Write LUKS-encrypted master backup to Drive #3 |
| `keytocard_one` (×4) | Add per-NK signing subkey, `keytocard` to that NK's OpenPGP applet, set UIF=on, validate |
| `pubkey_export` | Export armored public key + revocation cert to Drive #3 |
| `c6_piv` | NK#1 PIV slot 9c: rotate management key to AES-256, generate vendor keypair, self-sign cert, write to slot |
| `ethan_pack` | Sanitize working-state, copy ethan-pack to Drive #2 for secondary maintainer |
| `wrap` | Final state capture; emit operator instructions for shutdown + post-ceremony actions |

Operator role during execution is bounded: insert the requested Nitrokey when prompted, type the requested PIN, **touch the Nitrokey when it blinks** (UIF policy means every card-touching operation requires a physical touch). Everything else is the script.

### 2.2 Validation: `validate.py` is the ship gate

After all stages complete, `validate.py` runs five validation sections; the ceremony is considered complete only when all five report **0 failures**:

| Section | What it confirms |
|---|---|
| 1. Master keyring | Master fingerprint matches expected; 4 sign subkeys present; 1 enc sub present; all subs are stubs (private material on cards, not on disk) |
| 2. Per-NK card binding | For each NK#1-#4: card-status sig key matches a stub on the master keyring (with-colons field-14 AID match, not substring) |
| 3. All-NK cross-binding | Aggregate check that exactly 4 cards have been keytocard'd, no orphan stubs, no disk-resident duplicates |
| 4. Drive #3 (output) | LUKS backup file present + sized correctly; revocation cert present; pubkey ASCII-armor present |
| 5. Pubkey roundtrip | Imported pubkey verifies against itself; round-trip from Drive #3 → fresh keyring → all subs visible |

Any failure halts the ceremony in a state recoverable by the next `--from-stage` run. The ceremony is *not* "done" until validate.py emits the 0-failures verdict.

### 2.3 Resume semantics

Tails is amnesic. If the laptop reboots or crashes mid-ceremony, the master keyring (in RAM) is gone unless backups have already been taken. `ceremony.py --from-stage N` resumes from a named stage, but pre-stage-checks insist on:

- `values.txt` (RAM-only, recreated each session) populated with master_pass + luks_pass + per-NK PINs (`--from-stage > 1`)
- Existing master keyring at `~/ceremony/gnupg-master/` (`--from-stage > 4`) — re-derivable only by restoring from the LUKS backup on Drive #3 if the keyring is gone

Practical reading: the master is real once `luks_backup` (stage 5) completes. Earlier crashes mean restarting from `--from-stage 1`.

### 2.4 Operational runbook (private maintainer reference)

The full operational detail — Tails GRUB cmdline, per-step screenshots, expected output blocks for cross-check, sub-second timing for blink-touch — lives in the private maintainer runbook at:

```
~/intergenos/research/ceremony/runbook_v2_validated_2026-05-04.md
```

This is the v2.3-validated runbook owner-reviewed pre-ceremony; not in the public repo because it contains operational specifics that aren't reviewer-relevant.

### 2.5 Why automation, not manual

The ceremony is not a procedure for arbitrary maintainers — it is a procedure that runs at most every two years, by the primary maintainer, on a specific air-gapped host, to produce specific artifacts. The cost calculus favors automation:

- Manual `gpg --edit-key` sequences are recoverable in principle but expensive in practice (Drive #3 wipe + paperkey transcribe + LUKS restore + re-keytocard cascade).
- Automation lets us bake the lessons of the *first* attempt into the *second* attempt without paying a fresh cognitive tax each time.
- `validate.py` as a ship gate makes "did we do it right?" a binary check, not a multi-page eyeball pass.
- The 24+ ratified bug fixes documented in `docs/research/ceremony/lessons-learned-2026-05-05.md` are encoded in `ceremony.py` directly — they don't depend on the operator remembering to do something differently from the runbook.

The doc you are reading exists for reviewers. The operational truth is in the script. Both are kept consistent through the lessons-learned post-mortem; the v3 refactor of `ceremony.py` is scheduled for the next subkey rotation (2028-05-04 or earlier on compromise).

### 2.6 Development methodology

The automation was not developed on the air-gapped target. It was developed in a Docker container of Tails on the maintainer workstation, with the four production Nitrokeys connected for live validation. Each stage was iterated, debugged, and validated against real hardware in that container until `validate.py` returned the 0-failures verdict. Only then was the validated script transferred to Drive #2 and run inside the actual air-gapped Tails session.

This approach minimizes the risk window during the live ceremony in two ways. First, the air-gapped session is not where bugs get discovered — bugs get discovered in the dev container, in advance, where iteration is cheap. Second, the live ceremony is correspondingly short: the air-gap window only needs to cover the actual key-generating run plus validation, not the multi-hour debugging that necessarily attends a first-time procedure. Outside-contamination exposure scales with time-on-air-gap; the dev-container-first methodology shrinks the time-on-air-gap variable directly.

The motivating context: an initial manual attempt absorbed roughly eight hours of operator time before halting, with the irreversibility of `keytocard` operations meaning each fat-fingered command had a non-trivial recovery cost. The dev container moves the surface where mistakes happen out of the irreversible side of the air-gap boundary and into a reversible side where they cost only iteration cycles.

The development environment itself is not part of the trust-anchor chain — it never holds production keys, only ephemeral dev-Nitrokey state used to validate the script's logic. The trust-anchor chain begins in the air-gapped Tails session running `ceremony.py` against the production Nitrokeys, and is rooted in the master keypair generated there.

---

## Part 3 — Post-ceremony (online side)

These steps run on the workstation after the air-gapped session ends.

### 3.1 Retrieve public artifacts from Drive #3

Mount Drive #3 (label `CEREMONY`) on the workstation as a normal FAT32 USB. The LUKS-encrypted master backup is just a file from FAT32's perspective — leave it on the drive; do not copy to a network-connected machine.

```
mkdir -p ~/ceremony-output
cp /run/media/<user>/CEREMONY/intergenos-release-key.asc ~/ceremony-output/
cp /run/media/<user>/CEREMONY/intergenos-vendor-cert.pem ~/ceremony-output/
```

### 3.2 Update `docs/signing-key.md`

Populate the canonical fingerprint publication page with:

- Master fingerprint
- All four sign-subkey fingerprints with their NK custody assignment
- Encryption-subkey fingerprint
- Vendor-cert SHA-256 with the NK#1 PIV slot 9c assignment

Commit and push as a normal repo change.

### 3.3 Publish the public key

```
gpg --import ~/ceremony-output/intergenos-release-key.asc
gpg --keyserver keys.openpgp.org --send-keys <master-fingerprint>
gpg --keyserver keyserver.ubuntu.com --send-keys <master-fingerprint>
```

Confirm the email-verification flow at keys.openpgp.org. The role-UID convention (`InterGenOS Project Signing Key (primary) <intergenos-primary@intergenstudios.com>`) means the verification email goes to the project-role address.

### 3.4 Wire into `sign-release.sh`

Set environment variables (or pass flags) so the release-signing script knows which keys to use:

```
export INTERGENOS_GPG_KEY_ID=<active-signing-subkey-fingerprint>
export INTERGENOS_PKCS11_URI="pkcs11:object=<piv-slot-9c-key-label>;type=private"
```

Test-sign a sample artifact per `docs/signing-procedure.md` to confirm the pipeline works end-to-end before any real release.

### 3.5 Append to project log

Per the InterGenOS project log convention, append the ceremony-completion record to the sessions log endpoint with: master fingerprint, sub fingerprints, vendor-cert SHA-256, and physical-custody disposition.

### 3.6 Physical storage actions

| Artifact | Destination |
|---|---|
| Drive #3 (with master-backup.luks) | On-site fireproof safe |
| Nitrokey #1 | Stays with primary maintainer |
| Nitrokey #2 | Off-site bank safe deposit box (next bank-business-day) |
| Nitrokey #3 | Secondary maintainer (overnight ship) |
| Nitrokey #4 | Secondary maintainer's fireproof safe (overnight ship) |
| Paperkey copy #1 | On-site fireproof safe (with Drive #3) |
| Paperkey copy #2 | Owner-decided second location |
| Drive #4 (sealed) | Spare-pool storage |

### 3.7 Drive #2 sanitization for handoff

Before Drive #2 leaves the air-gapped maintainer's hands (e.g., to ship to the secondary maintainer), the working-state sensitive content is removed:

- All ceremony scripts (`ceremony.py`, `bootstrap.sh`, `wipe.sh`, helpers, `__pycache__/`) shredded
- Paperkey transcripts, `values.txt`, trace logs, scdaemon/gpg-agent logs all confirmed shredded
- The slim `ethan-pack/` plus a rewritten `scripts/README.md` describing the slim toolkit are what remain

PINs travel separately from any drive (paper, different envelope/courier — defense-in-depth).

---

## Part 4 — Recovery branches and known failure modes

The automation handles the common card-state failure modes that historically broke manual runs (transient `pcsc_list_readers failed`, scdaemon stale state, card-status format inconsistencies, partial keytocard cleanup). The full post-mortem catalogue — 24+ ratified bug fixes across 8 categories (pcscd/scdaemon stack flakiness, gpg `--card-edit` BLAST input, PIN mechanics, parser bugs, atomicity / failure recovery, bootloader/firmware mode, drive / log persistence, idempotency) — lives in [`docs/research/ceremony/lessons-learned-2026-05-05.md`](../research/ceremony/lessons-learned-2026-05-05.md).

For ceremony-day operator-facing failure modes:

| Failure | Recovery |
|---|---|
| Tails won't boot past GRUB (blank display) | Add `nomodeset` to kernel cmdline; if persistent, fall back to alternate Tails version on Drive #4 |
| Nitrokey not detected after offline-debs install | Replug; `sudo systemctl restart pcscd`; confirm `lsusb` sees Nitrokey vendor:product. Automation's `revive_pcscd()` handles this routinely; manual restart is the escalation path |
| `keytocard` fails or appears to succeed but card lacks the key | The atomic-undo in `stage_keytocard_one` deletes the just-added sub on failure. Re-run `--from-stage 7` for that card |
| Validation fails Section 2 (per-NK card binding) | The script logs which card and which expected-vs-actual fingerprint mismatched. Re-run `--from-stage 7` for the affected card after `gpg --card-edit` → `admin` → `factory-reset` if necessary |
| PIV slot 9c key generation fails | The script tries `nitropy nk3 piv` (canonical, validated 2026-05-02 against `--experimental change-admin-key`) with `piv-tool` from OpenSC as the documented fallback. If both fail, halt and consult the workstation operator |
| Power loss before `luks_backup` (stage 5) | Master is gone; restart from `--from-stage 1`. The amnesic Tails environment ensures no partial master persists |
| Power loss after `luks_backup` but before `pubkey_export` (stage 9) | Restore master from LUKS on Drive #3; resume from `--from-stage <N>` |

Manual recovery paths are documented in lessons-learned but are not part of the operator-facing procedure: by project policy, manual ceremony steps are never a fallback. If the automation can't make progress, the ceremony halts and the script gets fixed — the operator does not start typing `gpg --edit-key` commands by hand.

---

## Part 5 — Glossary and cross-references

### Glossary

- **Master keypair** — the long-lived RSA-4096 PGP key that certifies all signing subkeys. Lives in offline backups (paperkey + LUKS USB) only; never on a hardware token directly.
- **Signing subkey [S]** — a child key under the master, with `[S]` (sign) capability. UIF touch-required, lives on a Nitrokey OpenPGP applet. Each redundant Nitrokey gets its own subkey.
- **Encryption subkey [E]** — generated by `gpg --full-generate-key` automatically alongside the master. Stays in software (Tails RAM during ceremony, LUKS backup after). Used for `security@intergenstudios.com` PGP-encrypted reports per `SECURITY.md`.
- **Revocation certificate** — a pre-generated revocation that can be published if the master is ever compromised. Generated alongside the master so a future-you with no master access can still revoke.
- **Paperkey** — a tool (`paperkey`) that extracts only the secret-key octets from a GPG export, producing a short OCR-friendly hex transcription. Recoverable by hand if the LUKS USBs are lost.
- **LUKS USB** — a USB drive carrying a Linux Unified Key Setup encrypted volume. The master + revocation are stored inside; the LUKS passphrase is the only barrier between someone with the USB and the master keypair.
- **PIV slot 9c** — the X.509 signing slot on a PIV-capable smart card. On Nitrokey 3, slot 9c can hold an RSA-2048 keypair generated on-card. Used for `sbsign` operations (kernel + GRUB EFI binaries).
- **Vendor cert** — the X.509 self-signed certificate that pairs with the PIV slot 9c private key. Public, distributed via `docs/signing-key.md`. End users enroll it via shim/MOK.
- **`keytocard`** — the `gpg --edit-key` command that moves a subkey's private material onto the OpenPGP applet of an inserted smart card. Irreversible — the private material no longer exists in the GPG keyring, only the stub does.
- **UIF (User Interaction Flag)** — Nitrokey OpenPGP applet setting that requires a physical button-touch for each signing operation. Set to `on` for all four signing subkeys.
- **`revive_pcscd()`** — automation helper that restarts the smartcard daemon stack (`gpgconf --kill scdaemon` + `systemctl restart pcscd`) before any card-touching operation. Routine; the manual `pcsc_list_readers failed` recovery becomes unnecessary.
- **Ship gate** — the `validate.py` 0-failures-across-5-sections verdict. The ceremony is not "done" until ship gate passes.

### Cross-references

- [`docs/signing-procedure.md`](../signing-procedure.md) — the post-ceremony release-signing operational runbook; assumes this ceremony has completed.
- [`docs/signing-key.md`](../signing-key.md) — the canonical fingerprint publication page.
- [`docs/research/installer/signing_key_custody_2026-04-18.md`](../research/installer/signing_key_custody_2026-04-18.md) — design rationale, alternatives evaluated, D1 decisions.
- [`docs/research/installer/ms_shim_sponsorship_2026-04-18.md`](../research/installer/ms_shim_sponsorship_2026-04-18.md) — Microsoft shim-review path (post-ceremony parallel track).
- [`docs/research/ceremony/lessons-learned-2026-05-05.md`](../research/ceremony/lessons-learned-2026-05-05.md) — full post-mortem: 24+ ratified bug fixes across 8 categories, architectural lessons for v3, process lessons.
- [`SECURITY.md`](../../SECURITY.md) — disclosure policy and trust-anchor compromise response.

### External references

- Tails Welcome Screen: https://tails.net/doc/first_steps/welcome_screen/
- Tails wireless / rfkill: https://tails.net/doc/advanced_topics/wireless_devices/
- Nitrokey 3 PIV: https://docs.nitrokey.com/nitrokeys/features/piv/certificate_management
- Nitrokey 3 OpenPGP: https://docs.nitrokey.com/nitrokeys/features/openpgp
- `paperkey`: http://www.jabberwocky.com/software/paperkey/
- OpenSC `piv-tool` (cross-vendor PIV CLI, working against Nitrokey 3 PIV): https://github.com/OpenSC/OpenSC
- Nitrokey `nitropy` (vendor-native PIV management tool): https://github.com/Nitrokey/pynitrokey
