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
