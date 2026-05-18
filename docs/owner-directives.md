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

---

## D-006 — Theming canonical single-source-of-truth: gschema package

- **Issued:** 2026-05-18T14:50:39Z by owner
- **Context:** Item 2.4 of build-system coordinator's matrix-scan 5-item walk. Three theming dconf-key writers fighting (gschema package, `/etc/dconf/db/system.d/`, `scripts/install-theming.sh`) had caused: theming dead-letter on installed systems (D-021/D-026), libadwaita bridge regular-file-instead-of-symlink (J-005), divergent `intergen-welcome` path (J-001), intra-gschema button-layout conflict (90_intergenos vs 92_intergenos-desktop opposite values), 22-package theming wave fail-verify-sources (J-018), firewall posture inversion (J-021). Owner ratified Option 1 (gschema package as SSoT; retire the other two writers).
- **Verbatim:**

  > OWNER DIRECTIVE: gschema package as SSoT. Retire install-theming.sh and the dconf-system-db overrides.

- **Decision-Encoded:**
  - **`intergenos-default-settings` gschema-override package is THE canonical SSoT for theming defaults.**
  - Theme choices (InterGenOS GTK theme, Cybernetic Blue icons, Bibata-Modern-Classic cursor, prefer-dark color scheme, button-layout, favorites, welcomer behavior — per A33 2026-05-03) ship as `.gschema.override` files under `/usr/share/glib-2.0/schemas/`; `glib-compile-schemas` runs at package install.
  - Industry pattern (Pop_OS `pop-default-settings`, Ubuntu `ubuntu-default-settings`, Fedora gnome-shell extensions).
  - **Defaults, not locks.** User can override per-account via `gsettings` / gnome-tweaks / dconf-editor. Prime Directive aligned.
  - **`scripts/install-theming.sh` is retired.** All theming-related `gsettings set ...` writes from that script migrate into the gschema-override package.
  - **`/etc/dconf/db/system.d/` overrides are retired** as a theming mechanism. The system-wide dconf-db approach is NOT the SSoT.

- **Supersedes:**
  - `docs/audit/2026-05-18-design-decisions-matrix.md` reconciliation-walk row #21 (J-008 / J-009 / J-014 theming SSoT)
  - `docs/audit/2026-05-18-remediation-plan.md` owner-decision-queue item #21
  - Class A drift rows resolved by the SSoT decision (annotations applied by other coordinators in their sections as they encounter them):
    - J-001 `install-theming.sh` divergent `intergen-welcome` path (the script is retired)
    - J-005 libadwaita bridge regular-file copy (the bridge files ship via the gschema-override package as symlinks)
    - J-018 22-package theming wave fail-verify-sources (routed through the SSoT package)
    - D-021 / D-026 `intergenos-default-settings` dead-letter on installed systems (the package is now the SSoT — installs correctly via `pkm install`)
    - J-027 Firefox dock favourite pinned in gschema but `firefox` is tier:extra (resolves via the SSoT package gating favorites by package-presence)

- **Out-of-scope follow-on (NOT covered by D-006 — separate decisions or coordinator-level calls):**
  - J-021 firewall posture inversion: `install-theming.sh` writes a policy=drop nftables ruleset conflicting with canonical `core/nftables` policy=accept. With `install-theming.sh` retired, the firewall logic needs a new owner — likely `packages/core/nftables` post_install OR a separate `packages/security/intergenos-firewall-defaults/` package. Coordinator-level call (this is separate from theming SSoT). Surfaced for follow-on.

- **Implementation backlog (not directive surface):**
  - Author/finalize `packages/desktop/intergenos-default-settings/` recipe with full `.gschema.override` content
  - Migrate every theming-related dconf key from `install-theming.sh` and any `/etc/dconf/db/system.d/` files into the canonical gschema-override files
  - Reconcile intra-gschema button-layout conflict (single canonical value in the SSoT package)
  - Ship libadwaita bridge as symlinks via the SSoT package (fixes J-005)
  - Re-tarball the 21-package theming wave (J-018 missing tarball generators) — every package routes through the SSoT
  - Delete `scripts/install-theming.sh` after migration is complete + verified
  - Add a pre-push gate or audit script that flags any new dconf-key writer outside the SSoT package
  - Route the now-orphaned firewall logic per the out-of-scope follow-on above

- **Status:** ACTIVE

---

## D-007 — SSH + root posture + live/install user credentials (Class A gate; blocks ISO creation)

- **Issued:** 2026-05-18T16:10:52Z by owner
- **Context:** Walk item 4 cross-pattern `post_install()` audit sweep surfaced a remote-rootable-fresh-install exposure cluster: predictable SSH host keys baked at build time (`packages/core/openssh/build.sh:82` + `scripts/create-image.sh:280`), `PermitRootLogin yes` baked at build time (`packages/core/openssh/build.sh:79`), and a hardcoded root password `intergenos` baked at build time (`packages/core/shadow/build.sh:70`). Combined effect: every shipped install was remotely-rootable with publicly-known credentials over an SSH server with publicly-known host keys before the user logged in once. Owner-issued binding directive to lock down the SSH/credentials posture and gate ISO creation on compliance.
- **Verbatim:**

  > OWNER DIRECTIVE:
  > SSH enabled for USER ONLY, NOT root.
  > NO SSH BY ROOT ALLOWED ON ANY LANE.
  > NO PRE-INSTALLED SSH KEYS AT ANY TIME, ON ANY LANE, EVER.
  > For the LIVE USER: sudo capable user, username: intergenos, password: intergenos. This removes the apparently confusing randomly-generated password situation, and follows what I've personally observed other distros do for their 'live versions' (if they even use a password).
  > For TUI AND GUI install lanes: sudo capable user, username and password are chosen by the user DURING install.
  > Anything other than what is described above is a VIOLATION and needs to be vaporized immediately. Please capture that this is now a gate, and that our code needs to reflect this before another ISO can be created.

- **Decision-Encoded:**

  **SSH server posture (every lane: live ISO, qcow2, installed system):**
  - `sshd` enabled (USER may SSH in)
  - `sshd_config` MUST contain `PermitRootLogin no` explicitly (not commented; not relying on upstream default)
  - **No SSH host keys may be pre-generated at build time.** First-boot keygen path via `sshd.service` ExecStartPre is the only acceptable host-key origin. Build-time `ssh-keygen -A` calls are violations.
  - **No `authorized_keys` files may be pre-installed anywhere.** No `/root/.ssh/`, no `/home/*/.ssh/`, no SPOC/coordinator dev keys, no GitHub deploy keys, no SaaS keys — nothing baked.

  **Root-account state (operator-confirmed in same session, 2026-05-18T16:30Z):**
  - Root is **locked** on shipped installed systems (`*` in `/etc/shadow`; no valid password).
  - **User-sudo is the only acceptable path to root privileges.** No password-based root login, no SSH-as-root (covered above), no console-as-root via a known credential.
  - Build-VM chroot may retain whatever password the build pipeline needs for internal operations; the squashfs delivery must ship root locked.
  - **Distro precedent:** Matches Ubuntu and Fedora defaults — both lock root and route all privilege escalation through the user-chosen sudo-capable account. This is the mainstream secure-default pattern in production today; build-system coordinator confirmed no more-secure alternative pattern exists at issuance.

  **LIVE ISO user account:**
  - Username: `intergenos`
  - Password: `intergenos`
  - Sudo-capable (`wheel` group; configured sudoers entry)
  - **No randomly-generated passwords. No empty passwords. No tty root-autologin.** The live ISO's existing tty2 root-autologin in `installer/init/init.sh:303-315` is a violation under D-007 and must be either deleted or replaced with `intergenos`-user-autologin.
  - **Distro precedent:** This pattern matches Debian Live (`user:live` for 15+ years). It is a minority pattern vs the Ubuntu/Fedora/Mint empty-password+passwordless-sudo+auto-login majority pattern, but it is well-established and NOT opposite of any existing case.

  **TUI and GUI install lanes:**
  - Forge installer prompts the user for **username + password during install**.
  - That user is sudo-capable (`wheel` group).
  - The installer must not pre-populate a default username or password.
  - Root remains locked on the installed system per above.

  **Gate enforcement:**
  - This directive is a **Class A gate** that blocks ISO/qcow2 creation. Any artifact built before all code is brought into D-007 compliance is non-shippable.
  - Build-system coordinator authors a pre-build / pre-ISO gate script (`scripts/check-d007-compliance.sh`) wired into `scripts/build-intergenos.sh` `phase_image` (and any ISO-assembly script) that fails the build if violations are present.
  - Gate checks at minimum: (a) no `ssh-keygen -A` outside a first-boot service unit; (b) `sshd_config` ships with `PermitRootLogin no` explicitly; (c) no `chpasswd` or `usermod -p` writes outside `scripts/create-image.sh` env-var-driven paths AND no hardcoded password literals in any `chpasswd` invocation across the tree; (d) no pre-installed `authorized_keys` files anywhere; (e) live-mode `intergenos:intergenos` credential setup is present in `installer/init/init.sh`.

- **Supersedes:**
  - `packages/core/openssh/build.sh:79` `sed -i 's/^#PermitRootLogin prohibit-password/PermitRootLogin yes/'` — coordinator strips this sed and ships an explicit `PermitRootLogin no` config
  - `packages/core/openssh/build.sh:82` `ssh-keygen -A` at build time — coordinator deletes
  - `scripts/create-image.sh:280` `chroot ... ssh-keygen -A` — coordinator deletes (qcow2 path)
  - `packages/core/shadow/build.sh:70-71` `echo "root:intergenos" | chpasswd` + `passwd -e root` — coordinator replaces with `passwd -l root` (lock root)
  - `scripts/create-image.sh:402` `ROOT_PASSWORD` env-var chpasswd — coordinator replaces with `passwd -l root` (qcow2 path)
  - `installer/init/init.sh:303-315` tty2 root-autologin — coordinator replaces with `intergenos`-user-autologin OR deletes (see decision-encoded above; F-006 HG-class audit row)
  - `installer/init/init.sh:236-272` live-mode user-setup section — coordinator replaces randomly-generated-password / empty-password logic with explicit `intergenos:intergenos` credential setup
  - Audit `docs/audit/2026-05-18-comprehensive-state-audit.md` rows: F-004 (hardcoded root password), F-006 (tty2 root-autologin), L-001-adjacent and any "live user randomly-generated password" surface
  - Any prior coordinator memory or reference doc that framed a "randomly-generated live password" or "empty live password" as the v1.0 approach

- **Composes with:**
  - **D-005** (UKI parity Option A) — the SSH/credentials directive is orthogonal to boot architecture; both compose cleanly
  - **D-001** (LUKS-at-install v1.0 scope) — root-lock on installed system composes with LUKS unlock semantics (LUKS handles pre-root-mount auth; user account handles post-root-mount auth)
  - **`feedback_no_dev_keys_in_shipped_iso`** (existing POWER memory 2026-05-14) — D-007 elevates this from coordinator-internal feedback to operator-ratified canonical, expanding "no SPOC dev keys" to "NO pre-installed SSH keys AT ANY TIME ON ANY LANE EVER"

- **Implementation backlog (informational; gate enforcement makes this the ISO-blocking path until done):**
  - Code fixes per the supersession list above
  - `scripts/check-d007-compliance.sh` authoring + wiring into `phase_image` + ISO-build path
  - Forge installer review: confirm user/password prompt + wheel-group assignment + root-lock are all live in installer flow
  - `sshd_config` audit: ensure `PermitRootLogin no` is the shipped state (consider a drop-in at `/etc/ssh/sshd_config.d/00-intergenos-d007.conf` so future upstream-config-rebases don't silently revert)
  - TRACKER.md gate entry under v1.0 ship-blockers

- **Status:** ACTIVE — **CLASS A GATE; blocks ISO/qcow2 creation until compliance verified**

---

## D-008 — InterGen provenance-gated tool dispatcher (Class A v1.0 ship-block; blocks ISO creation when InterGen is included)

- **Issued:** 2026-05-18T16:49:42Z by owner
- **Context:** Walk item 5B (InterGen `manage_services` LLM-root-equivalence) generalized by owner during the walk to a broader prompt-injection defense. Owner-proposed concept: any ingress-directional action (tool calls sourced from content the user did not author) gets presented for explicit user review before execution. Build-system coordinator confirmed the concept maps to published LLM-security research (Anthropic + Microsoft spotlighting / instruction-hierarchy work) and is complementary to D-007 Option A (pkexec for authentication). Owner greenlit v1.0 minimum scope; build-system coordinator drafted RFC at `docs/architecture/intergen-provenance-gate-design.md` (commit `b3949b24` 2026-05-18) and surfaced the directive text for owner firing. Owner fired the directive at this timestamp.
- **Verbatim:**

  > OWNER DIRECTIVE:
  >
  > InterGen ships a provenance-gated tool dispatcher. Every tool call carries a declared and verified provenance label (user_direct / user_implied / ingress_derived). Actions sourced from ingress content the user did not author are held for explicit user review before execution, with the proposed action + source content displayed for the user's decision. Privileged actions then route through pkexec for authentication (composes with D-007), so prompt injection plus credential-cache ride is gated by two independent human-decision points.
  >
  > Canonical design: docs/architecture/intergen-provenance-gate-design.md.
  >
  > Scope: v1.0 minimum is required for ship — the gate, the dispatcher, the review modal, the mechanical ingress-tool watermark, the audit log. v1.0 InterGen ISO does not ship until this lands; reuse the D-007 gate pattern (compliance check script + phase_image hook) to enforce. v1.x full (spotlighting of retrieved content, per-conversation trust state, output-side pattern scanning) is top-of-ToDo and tracked at TRACKER K16. Closes audit I-027 (SafetyTier.CONFIRM not enforced) and I-035 (manage_services LLM-root-equivalence) as the architectural resolution path; partially closes I-029. No code lands until installed-system + Windows-host + build-system coordinator peer review converges on the RFC.
  >
  > Anything that ships InterGen tool dispatch without this gate is a VIOLATION and must be vaporized before the next ISO is created.

- **Decision-Encoded:**

  **Architectural commitment:** InterGen's tool dispatcher is rewritten to gate every tool call by a declared-and-verified provenance label. The canonical design document at `docs/architecture/intergen-provenance-gate-design.md` is the source-of-truth for the dispatcher's shape, the taxonomy, the verification mechanism, the UX, and the v1.0-vs-v1.x scope split. Any departure from that design requires either an RFC amendment (with peer review) or a superseding directive.

  **v1.0 minimum scope (required for ship):**
  - Three-category provenance taxonomy on every tool call (`user_direct` / `user_implied` / `ingress_derived`); no-fallback policy at dispatcher
  - Dispatcher gate with the behavior matrix at RFC §6 (read-only auto-allowed; state-changing user-scope held for non-`user_direct`; privileged state-changing held + pkexec on allow)
  - Mechanical ingress-tool-watermark verification (RFC §5.1) — LLM self-declaration is necessary but not sufficient; if any ingress tool fired earlier in the turn the heuristic escalates the effective label by one tier
  - Review modal with proposed action + source content + LLM reasoning available to the user; Allow once / Allow for this conversation / Deny
  - Notification fallback for held actions when session is locked (RFC §7.2)
  - System-prompt extension requiring `source_of_request` field on every tool call (RFC §8)
  - Per-tool-call audit log at `$XDG_STATE_HOME/intergen/tool-dispatch.jsonl` (RFC §9)
  - Integration with D-007 pkexec gate — provenance gate runs BEFORE pkexec; both must pass for privileged actions
  - Compliance check script + `phase_image` hook (same pattern as `scripts/check-d007-compliance.sh`); refuses to assemble ISO if InterGen is included without the gate

  **v1.x full scope (top-of-ToDo backlog at TRACKER K16):**
  - Spotlighting of retrieved content — `<UNTRUSTED-INGRESS source="...">...</UNTRUSTED-INGRESS>` markers wrapping all ingress-tool output in the LLM's context window
  - Per-conversation trust state (denied tool+source combinations stay denied for the conversation)
  - Cross-conversation policy (user-set allowlists / denylists)
  - Output-side scanning — elevate the v1.0 advisory pattern-detection (RFC §5.2) to gating after FP rate calibration
  - `intergen tool-log` CLI for user review of their dispatch history

  **Composition with prior directives:**
  - D-001 (LUKS v1.0) — orthogonal
  - D-005 (UKI parity) — orthogonal
  - D-006 (theming SSoT) — orthogonal
  - D-007 (SSH + root + credentials) — **composes**. Provenance gate is upstream of pkexec; pkexec is upstream of execution. Together: intent-verified + authentication-verified. Two independent human-decision points for any privileged action sourced from ingress content.

  **Peer-review gate:** No InterGen tool-dispatch code lands until installed-system coordinator (technical review of dispatcher + tool-call schema + system-prompt extension) + Windows-host coordinator (cross-host review + documentation surface) + build-system coordinator (integration with pkexec + ISO compliance gate) converge on the RFC. RFC v0.1 currently at `docs/architecture/intergen-provenance-gate-design.md` commit `b3949b24`; review may produce v0.2 with deltas before code begins.

- **Supersedes:**
  - audit `docs/audit/2026-05-18-comprehensive-state-audit.md` row I-027 (`SafetyTier.CONFIRM` never enforced) — the provenance gate IS the proper CONFIRM-tier implementation; the existing broken `safety == BLOCKED`-only check at `intergen/tool_registry.py:99-115` is rewritten as part of K15
  - audit row I-035 (`manage_services` sudo credential-cache ride) — two-layer fix: D-007 already locked the F-004 wheel-NOPASSWD assumption; K15 routes manage_services through pkexec with policy forcing per-action password + MANAGEABLE_SERVICES allow-list + critical-unit refusal (firewalld, NetworkManager, dbus, polkit, systemd-logind, sshd)
  - audit row I-029 (`safety.classify_command` imported never invoked) — partial; gate dispatcher consults the tier so dead code becomes live. Residual decision on canonical classifier flagged for installed-system coordinator during K15 peer review
  - Any prior framing of InterGen's tool dispatch as "ships secure-by-default" without explicit provenance verification — that framing is retired

- **Class A enforcement:**
  - This directive is a **Class A v1.0 ship-block**: ISO assembly that includes InterGen without the v1.0 minimum gate fails. Enforcement mirrors D-007's pattern.
  - Build-system coordinator authors `scripts/check-d008-compliance.sh` (or extends `check-d007-compliance.sh`) and wires it into `phase_image` + `build-iso.sh` as part of K15 implementation work.
  - Operator may build / boot / ship ISOs WITHOUT InterGen during the K15 implementation window — the gate only fires when InterGen is included in the squashfs.

- **Status:** ACTIVE — **CLASS A v1.0 SHIP-BLOCK** (blocks ISO/qcow2 creation when InterGen is included in the artifact until v1.0 minimum gate compliance is verified)
