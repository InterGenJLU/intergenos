# Owner Directives — append-only log

This file is the canonical record of explicit directives issued by the project owner. Every entry is **append-only**. Entries are never edited, reordered, or deleted — superseding directives are added as NEW entries that reference the prior entry.

## Protocol

The owner issues a directive by prefixing a message with `OWNER DIRECTIVE:`.

When any coordinator (build-system, installed-system, Windows-host, or any future fleet member) sees that prefix in owner input or on the coordination bus, they MUST:

1. **Acknowledge immediately** in the same thread or message.
2. **Append the verbatim directive text** + UTC timestamp + the originating thread/context to this file as a new numbered entry.
3. **Cite this file as source-of-truth** in any future synthesis, matrix row, tracker entry, audit pass, design doc, or commit message that touches the directive's subject.
4. **Update any conflicting prior records** by adding a `SUPERSEDED-BY` annotation pointing to the directive entry. Do not silently rewrite prior records.

The recording in this file is the load-bearing artifact. A coordinator that fails to record breaks the trust contract. A coordinator that records but then writes contradicting "deferred" language elsewhere breaks the trust contract.

## What counts as a directive (vs a discussion)

The `OWNER DIRECTIVE:` prefix is the only signal. Without it, owner messages are interpreted in their conversational context (questions, requests, suggestions, authorizations). With it, the message is a binding ratification — to be recorded, never re-litigated.

## What coordinators MUST NOT do

- Write "DEFERRED", "post-v1.0", "v1.x", "out of scope for v1.0", "Phase 2", or equivalent scheduling language in any tracker, design doc, matrix row, research note, or commit message WITHOUT citing a specific entry in this file as the authorizing directive. If no such entry exists, frame as `PROPOSED-DEFERRAL — awaiting operator confirmation` and surface for input.
- Edit, reorder, or delete entries below. Supersession is an additive operation.
- Add entries on the owner's behalf without their explicit `OWNER DIRECTIVE:` prefix in the originating message.
- Treat coordinator-side "we'll get to it later" or "out of cycle scope" as equivalent to an owner ratification of deferral. They are not. They are operating notes; this file is owner state.

## Format

Each entry uses this shape:

```
## D-NNN — <one-line summary>

- **Issued:** <ISO 8601 UTC timestamp> by owner
- **Context:** <thread / conversation reference where the directive was given>
- **Verbatim:**

  > <verbatim text following the OWNER DIRECTIVE: prefix>

- **Supersedes:** <list of prior records this overrides — file paths + line refs, or "none">
- **Status:** ACTIVE (default) | SUPERSEDED-BY D-NNN
```

`D-NNN` numbering is monotonic. First directive is `D-001`. Numbers are assigned at append time and never reused.

## Entries

## D-001 — LUKS-at-install is v1.0 scope

- **Issued:** 2026-05-18T14:06:42Z by owner
- **Context:** Matrix-scan-2026-05-18 reconciliation; owner response to build-system coordinator's measured-boot scope question. Live test of the `OWNER DIRECTIVE:` protocol established at `bb91efee` earlier the same day.
- **Verbatim:**

  > LUKS-at-install is v1.0 scope. Opt-in encryption checkbox in Forge; passphrase-only LUKS2 baseline. TPM-sealed unlock + FIDO2 unlock available as EXPERIMENTAL features, flagged as such in the installer UI (Ubuntu 24.04 precedent). LUKS installs get a tiny FDE-only initramfs (busybox + cryptsetup); plain installs keep the no-installed-system-initramfs path. Supersedes the 2026-04-05 LUKS deferral, the 2026-05-14 "LUKS is post-v1.0" tracker note, and the 2026-05-15 measured-boot P7-parking.

- **Supersedes:**
  - `docs/research/installer/installer_design_plan_2026-04-05.md` lines 67-72 + 248-253 — LUKS+LVM listed under "Future" / Phase 2
  - Owner-home tracker `TRACKER.md:1254` (2026-05-14 "LUKS is post-v1.0" rEFInd note) — coordinator-applied `SUPERSEDED-BY D-001` annotation directly. (Original wording mis-framed tracker maintenance as owner responsibility; corrected per owner-direct instruction — the build-system coordinator has maintained the tracker since inception. Correction note appended at the bottom of this entry.)
  - `docs/audit/2026-05-18-design-decisions-matrix.md` rows: BOOT "Measured-boot scope (TPM)" DEFERRED 2026-05-15 P7-parking; PARTITION "LUKS / LVM / BTRFS / ZFS / FDE" DEFERRED 2026-04-05; SECURITY "LUKS / FDE at install time" UNKNOWN/DEFERRED-to-v1.x; PARTITION "LUKS / encryption-at-rest for v1.0" UNKNOWN; BOOT "Measured-boot scope (PCR / TPM-sealing)" PROPOSED
  - `docs/audit/2026-05-18-remediation-plan.md` items #2 measured-boot scope, #7 F-013/B-050 MOK TPM sealing v1.0 ship-decision, #32 N-018 encryption-at-rest
- **Narrows (does NOT fully supersede):**
  - 2026-04-09 "no installed-system initramfs" ratification — STILL ACTIVE for plain installs. LUKS installs get a tiny FDE-only initramfs (busybox + cryptsetup) as a narrow exception. This is a NARROWING, not a supersession.
- **Implementation scope (informational — execution backlog, not directive surface):**
  - Forge UI: opt-in encryption checkbox at partition stage; passphrase entry; EXPERIMENTAL banner on TPM-seal + FIDO2 sub-options
  - Kernel: `CONFIG_DM_CRYPT=y` + crypto API built-in (not module)
  - Live ISO: `cryptsetup` available to the installer
  - FDE initramfs: custom busybox + cryptsetup-static; ~50 lines of init in the spirit of `installer/init/init.sh`; only built and installed for LUKS-enabled installs
  - Recovery story documented in `docs/users/security-defaults.md`
- **Status:** ACTIVE
- **2026-05-18T14:22:53Z correction note (build-system coordinator):**
  The original Supersedes entry for `TRACKER.md:1254` read "NOT edited by coordinator (home-drive content per project rule); surfaced to owner for tracker update." That wording was incorrect. The build-system coordinator maintains the owner's TRACKER.md; the owner has had the coordinator do this since the tracker's inception. The `SUPERSEDED-BY D-001` annotation has now been applied directly to `TRACKER.md:1254` by the build-system coordinator. The same correction applies to the bus broadcast at 2026-05-18T14:10:46Z and the commit message of `897e1f0e`, which also used the wrong framing. The operator-direct correction that triggered this note is recorded in coordinator-internal feedback memory.

---

## D-002 — Item #1 B-001 SHIM path ratification encoded for citation symmetry

- **Issued:** 2026-05-18T14:22:53Z by owner (formally encoded as directive at this time; substance conversationally greenlit earlier in the same session at approximately 13:25 UTC)
- **Context:** Item 1 of the build-system coordinator's matrix-scan 5-item walk. Coordinator surfaced #1 (B-001 SHIM path), #22 (B-015 shim-review PR timing), #24 (L-007 per-archive sig) as ALREADY RATIFIED — vaporize from open queue. Owner's conversational greenlight, then owner-direct 2026-05-18 ~14:20 UTC authorized formal encoding of those three greenlights as D-NNN entries so the citation trail is symmetrical with D-001.
- **Verbatim (conversational greenlight authorizing this directive entry):**

  > Ok to vaporize, clean them out :)

  And subsequently:

  > I'm authorizing YOU to encode the vaporized items as D-NNN items
- **Decision-Encoded:** B-001 SHIM path is RATIFIED. The 2026-04-18 D1-7 decision stands — ship via the Fedora-piggyback shim (bootstrap path) AND pursue our own MS-signed shim in parallel via the `rhboot/shim-review` PR. Both arms pre-authorized day-0. Cycle-5 ISO ships an InterGenOS-self-signed shim wiring NEITHER arm, but that is a wiring drift (implementation backlog), not a fresh decision. Vaporized from the remediation plan's owner-decision queue.
- **Supersedes:**
  - `docs/audit/2026-05-18-remediation-plan.md` owner-decision-queue item #1 B-001 SHIM path (annotated RESOLVED via D-002)
  - `docs/audit/2026-05-18-design-decisions-matrix.md` reconciliation-walk row #1 (annotated to cite D-002 instead of coordinator-classification "ALREADY RATIFIED")
- **Status:** ACTIVE

---

## D-003 — Item #22 B-015 shim-review PR timing ratification encoded for citation symmetry

- **Issued:** 2026-05-18T14:22:53Z by owner (formally encoded as directive at this time; substance conversationally greenlit earlier in the same session at approximately 13:25 UTC as part of the same response that covered D-002 and D-004)
- **Context:** Item 1 of the same matrix-scan walk that produced D-002. The greenlight covered three vaporize items in one operator response.
- **Verbatim (conversational greenlight authorizing this directive entry):**

  > Ok to vaporize, clean them out :)

  And:

  > I'm authorizing YOU to encode the vaporized items as D-NNN items
- **Decision-Encoded:** B-015 shim-review PR-open timing is RATIFIED. Target date 2026-05-22 stands; couples to D-002 (the shim path itself). Vaporized from the remediation plan's owner-decision queue.
- **Supersedes:**
  - `docs/audit/2026-05-18-remediation-plan.md` owner-decision-queue item #22 B-015 shim-review PR timing (annotated RESOLVED via D-003)
  - `docs/audit/2026-05-18-design-decisions-matrix.md` reconciliation-walk row #22 (annotated to cite D-003)
- **Status:** ACTIVE

---

## D-004 — Item #24 L-007 per-archive sig ratification encoded for citation symmetry

- **Issued:** 2026-05-18T14:22:53Z by owner (formally encoded as directive at this time; underlying decision originally ratified 2026-05-12 via multi-vantage RFC AGREE)
- **Context:** Same matrix-scan walk that produced D-002 and D-003. The underlying decision was already ratified 2026-05-12 via fleet RFC closure commit `d6b3946a` (`docs/architecture/per-archive-sig-decision.md`). This D-NNN entry encodes the closure for citation symmetry alongside D-002 and D-003.
- **Verbatim (conversational greenlight authorizing this directive entry):**

  > Ok to vaporize, clean them out :)

  And:

  > I'm authorizing YOU to encode the vaporized items as D-NNN items
- **Decision-Encoded:** L-007 per-archive `.sig` is RATIFIED as signed-index-only for v1.0; per-archive sigs deferred to v1.1+. The 2026-05-12 closure stands. Four remaining artifact drifts (`docs/mirror/design.md`, `scripts/mirror-publish.sh`, the apache snippet at `apache-userdata-snippet.conf:80-83`, and one additional surface per Windows-host iter-2 finding) still need an artifact-sweep, but that is implementation backlog, not a decision. Vaporized from the remediation plan's owner-decision queue.
- **Supersedes:**
  - `docs/audit/2026-05-18-remediation-plan.md` owner-decision-queue item #24 L-007 per-archive `.sig` (annotated RESOLVED via D-004)
  - `docs/audit/2026-05-18-design-decisions-matrix.md` reconciliation-walk row #24 (annotated to cite D-004)
- **Status:** ACTIVE

---

## D-005 — Installed-system boot architecture: Option A (UKI parity, signed by user's MOK)

- **Issued:** 2026-05-18T14:39:52Z by owner
- **Context:** Item 2.2 of build-system coordinator's matrix-scan 5-item walk. After D-001 narrowed the no-installed-system-initramfs ratification to make room for LUKS, the residual question was: should installed systems use UKI parity with the live ISO (Option A) or stay on grub-loads-vmlinuz (Option B)? Coordinator's initial Option-A presentation muddled key boundaries; owner caught the bug ("We're not going to expose our signing infrastructure to users"). Coordinator clarified: the InterGenOS PIV slot 9c key stays at HQ; per-machine signing uses the user's own MOK (same trust pattern DKMS already uses on InterGenOS). Subsequent web research validated Option A is the documented Arch Linux Secure-Boot pattern (mature, production-grade tooling: `ukify`, `sbsigntool`, `mkinitcpio` post-hooks, `sbctl`) and aligns with Fedora's Phase 2 UKI roadmap. Owner ratified.
- **Verbatim:**

  > OWNER DIRECTIVE: Option A wins for UKI. Arch is already there, Fedora is on the way, and everyone else is playing catch up. We're going to start on the right foot with this one, and possibly be the first to automate it :)

- **Decision-Encoded:**
  - **Installed systems use UKI parity with the live ISO.** Per-kernel UKI on the ESP. shim → GRUB → UKI chain (matches existing live ISO chain shape); shim-direct-to-UKI is a future-option, not v1.0 baseline.
  - **User-MOK signing.** The user's machine-local MOK key (Forge generates this at install time; user enrolls via MokManager at first boot — existing Forge flow) signs UKIs at kernel install + upgrade time. **The InterGenOS PIV slot 9c key NEVER leaves HQ.** No InterGenOS signing material lives on user systems.
  - **Forge installs UKI-build tooling** on the installed system: `ukify`, `sbsigntool`. NOT the InterGenOS vendor cert/key — only the user's MOK material exists on the user's machine.
  - **`packages/core/linux-kernel` post_install hook** runs `ukify` + sign-with-user-MOK on every kernel install and upgrade. Old UKIs cleaned up on kernel removal (`pkm remove linux-kernel-X.Y.Z`).
  - **ESP sizing.** Forge enforces minimum ESP headroom for multiple UKI generations (per-kernel UKI ~80-150 MB; default keep-2-old-kernels target ~500 MB minimum ESP).
  - **Fallback path** on UKI signing failure: kernel + initrd remain on disk; the build-system ships a grub-loads-vmlinuz boot entry as a recovery fallback. Fails closed but recoverable.

- **Composition with D-001 (no conflict — they compose):**
  - LUKS installs (per D-001): UKI's bundled initramfs IS the tiny FDE-only initramfs (busybox + cryptsetup; passphrase prompt). UKI loads → FDE initramfs unlocks → root mounts → kernel handoff.
  - Plain (non-LUKS) installs: UKI's bundled initramfs is empty / minimal (just microcode cpio, no FDE userspace needed). Kernel-builtin storage drivers + PARTUUID + rootwait still work as ratified 2026-04-09. No change to plain-install root-mount semantics.

- **Supersedes:**
  - `docs/audit/2026-05-18-design-decisions-matrix.md` BOOT row "UKI / GRUB model" (formerly "UKI for live-ISO; GRUB-with-shim chainload for installed-system" — now "UKI for both")
  - `docs/audit/2026-05-18-design-decisions-matrix.md` BOOT row "UKI vs grub — installed system" (formerly PROPOSED)
  - `docs/audit/2026-05-18-design-decisions-matrix.md` reconciliation-walk row #3 (B-008 / B-026)
  - `docs/audit/2026-05-18-remediation-plan.md` owner-decision-queue item #3 B-008 / B-026 installed-system boot architecture
  - Matrix B-047 vmlinuz signing path drift (was Class B doc/code drift: doc said distro-EFI signs vmlinuz; code MOK-signed at install — D-005 collapses to a single scheme: user-MOK signs UKIs at install + kernel upgrade)
  - Windows-host iter-2 W-B12 / W-B13 (UKI live-ISO vs grub-loads-vmlinuz installed-system Class B cross-time conflict) — RESOLVED via UKI everywhere

- **Implementation backlog (informational, not directive surface):**
  - `packages/core/linux-kernel` post_install hook authoring with edge-case handling (kernel-update mid-LUKS-unlock; partial download; ESP-full; MOK key missing → fall back to grub-loads-vmlinuz; signing failure → fall back)
  - Forge UKI-tooling installation step (ukify, sbsigntool ship in installed system; MOK material generated locally)
  - Forge ESP sizing enforcement at partition stage
  - GRUB menu generation that picks up UKIs from `/EFI/Linux/` (or `/boot/efi/EFI/<vendor>/`) per UAPI conventions
  - Recovery-fallback grub entry generation
  - Microcode-in-UKI fix (E-001/E-002 in T0-1 cluster) — must land for live ISO regardless; D-005 inherits the fix
  - User-facing docs in `docs/users/security-defaults.md` covering UKI-update workflow + recovery story
  - `packages/core/linux-kernel` post_remove hook to clean up stale UKIs

- **Status:** ACTIVE
