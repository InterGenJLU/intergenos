# InterGenOS — Design Decisions Matrix

**Started:** 2026-05-18 (Monday morning CDT)
**Trigger:** Owner observation 2026-05-18 ~06:50 CDT — this morning's remediation plan surfaced items as "open" that were in fact ratified weeks/months ago. Three concrete examples: T0-2 (MOK-until-MS-shim was the plan from day 0; PR scheduled 2026-05-22), T0-3 (dracut vs PARTUUID decided after a full day of discussion previously), T0-4 (SSH host-key baking was explicitly directed AGAINST by owner — represents a drift FROM a stated directive).
**Coordinator:** SPOC
**Participating coordinators:** SPOC, IGOSC, WC. All three scan their host-local memory + their angle on the shared repo + VPS resources, then push findings into their section. SPOC synthesizes the cross-host conflict list.
**Companion docs:** [audit](2026-05-18-comprehensive-state-audit.md) (~662 raw findings), [remediation plan](2026-05-18-remediation-plan.md) (17 clusters, 38 owner decisions). Once this matrix is populated, the remediation plan's owner-decision queue gets cross-checked against ratified decisions captured here.

---

## Scope

Each coordinator scans their HOST-LOCAL state plus their angle on shared resources:

### Build-system coordinator
- Local memory tree (operator's local agent-memory directory)
- In-repo docs: `/mnt/intergenos/docs/` (architecture, research, governance, operations, signing-procedure, etc.) + README + CLAUDE.md
- Operator's living tracker + archives (under operator home)
- VPS canonical rules: `intergen://rules/canonical/*` (12+ files)
- VPS reference: `intergen://reference/*` (12 files now incl. operations runbook)
- VPS working/ dir contents (if any)
- Commit history relevant to design decisions
- SPECIFIC PRIORITY HUNT: SSH host-key directive provenance + regression introduction commit

### Installed-system coordinator
- Host-local memory tree
- Host-local carryovers + handoffs + reorient files
- Same repo (mounted)
- Same VPS resources

### Windows-host coordinator
- Host-local memory tree
- Host-local carryovers + handoffs + reorient files
- Same repo (via private overlay network)
- Same VPS resources

---

## Categorical taxonomy

Each scanned decision gets categorized so the matrix sorts cleanly:

| Category | Examples |
|---|---|
| **BOOT** | Shim path, signing chain, UKI/grub model, MOK strategy, initramfs choice (dracut), measured-boot scope |
| **PARTITION** | Disk strategy (LUKS/LVM/BTRFS/ZFS), swap default, encryption-at-rest posture, alongside-install scope |
| **SECURITY** | SSH posture, root account, default firewall, kernel hardening, AppArmor scope, lockdown, password policy |
| **PACKAGE-MGR** | pkm trust model, signed mirror, GPG keyring, upgrade safety, channels, anti-rollback |
| **INSTALLER** | Forge architecture, GUI/TUI parity, package groups, hook framework, locale generation |
| **AI/INTERGEN** | Service model, safety classifier, D-Bus auth, model selection, semantic layer, voice |
| **DESKTOP/UX** | First-boot animation, intergen-welcome, theming, GNOME defaults, GDM session policy |
| **LEGAL** | License posture, GPL source-availability, SPDX, DCO, trademark, BIS, redistribution |
| **BUILD** | Reproducibility, orchestrator scope, test infrastructure, signing ceremony automation |
| **DOCS/MIRROR** | Public hosting, repo hosting, doc layout, runbook canon |
| **PROCESS** | Fleet coordination, MCP namespaces, peer-review, succession, owner-direction discipline |

---

## Decision status states

- **RATIFIED** — owner explicitly approved (with date + provenance pointer)
- **PROPOSED** — captured in design doc, not yet ratified
- **SUPERSEDED** — earlier decision replaced by later one (link both)
- **DEFERRED** — explicitly v1.x or post-v1.0
- **VIOLATED** — code/runtime state diverges from ratified decision (THE category the SSH host-key class lives in)
- **UNKNOWN** — surfaced in audit but no decision found in memory/tracker/docs

---

## Schema per row

```
| Category | Topic | Decision | Status | Date | Ratified by | Source | Conflicts |
```

- **Category** — from taxonomy above
- **Topic** — short hook (e.g., "SSH host keys", "shim path", "first-boot animation")
- **Decision** — the actual position (plain English)
- **Status** — from status states above
- **Date** — when ratified / proposed / superseded
- **Ratified by** — owner / coordinator consensus / individual coordinator
- **Source** — file path or memory ID with line ref if possible
- **Conflicts** — IDs of other rows that contradict this one

---

## Build-system-coordinator findings

**Scan posture:** iter-1 via 5 parallel topic sub-agents (host-local memory + carryovers, in-repo docs, operator's living tracker + archives, VPS resources, SSH host-key regression hunt) + a commit-history sweep. Mostly concurs with the installed-system-coordinator's class-A drift list above. Unique contribution this iteration is the **regression-introduction forensics for T0-4 SSH host-key baking** plus a pattern observation that bears on the broader trust-calibration question.

### Headline — SSH host-key baking regression-introduction forensics

Two distinct sites generate the host keys; both predate the explicit POWER RULE memory (2026-05-14) by 5-6 weeks. Same model co-author trailer in both commits. Same mental-model bug: the author believed `post_install` hooks and image-creation steps run on the user's installed system; they run in the build chroot.

| # | Site | Commit | Date | Path | Effect |
|---|---|---|---|---|---|
| 1 | OpenSSH `post_install` hook | `ff9ed5f3` | 2026-04-05 | `packages/core/openssh/build.sh:82` | `ssh-keygen -A` writes `/etc/ssh/ssh_host_{rsa,ecdsa,ed25519}_key{,.pub}` into chroot during `core-extra` phase |
| 2 | image-creation chroot block | `27ce4ca9` | 2026-04-08 | `scripts/create-image.sh:280` | `chroot $MOUNT_POINT /bin/bash -c 'ssh-keygen -A 2>/dev/null'` writes same keys into the mounted target rootfs |

**The defensive layer already exists** at `config/systemd/sshd.service:29`:

```ini
ExecStartPre=/bin/bash -c 'test -f /etc/ssh/ssh_host_ed25519_key || ssh-keygen -A'
```

This was committed in the same `ff9ed5f3` and is the correct fix. Removing both `ssh-keygen -A` build-time invocations would let `ExecStartPre=` self-bootstrap unique host keys on each user's first boot. Currently the `test -f` guard succeeds (pre-baked keys present) and first-boot keygen is bypassed.

**Squashfs scrub absent.** `scripts/build-squashfs.sh` "Clean runtime trash" step truncates `/var/log/*`, removes `/tmp/*`, resets `/etc/machine-id` to `uninitialized`, and removes bash history — but does NOT scrub `/etc/ssh/ssh_host_*`. Adding that scrub closes the defense-in-depth gap at the squashfs-pack layer.

**Pattern hunt (parallel violations of the same directive).** Cross-checked the codebase for every other class enumerated in the directive memory:

- `authorized_keys` / `ssh-ed25519 AAAA…` literals in build path: **none** (only an operator-side fetch key in `scripts/download-sources.py:511`, not baked into deliverable)
- Hardcoded default user/root passwords: **retired** at `7900c941` (Path 4). **Residual** `intergenos` password in `packages/core/shadow/build.sh:70` is a package-level not-stripped — this matches the installed-system coordinator's F-004 finding.
- Pre-set liveuser account in squashfs: **clean** — `installer/init/init.sh:246` writes liveuser to overlay only (live mode), squashfs itself has no entry; comment at `init.sh:250` explicitly states "no pre-shipped keys" design intent
- API keys / webhooks / phone-home endpoints: **none** in build path
- Borderline: `make-ca -g` in `create-image.sh:286-289` generates `/etc/ssl/certs/ca-certificates.crt` from the bundled trust store — likely fine (standard Mozilla bundle) but worth confirming the bundle excludes any InterGenOS release-signing roots from the system trust store

### Cross-pattern audit recommendation (load-bearing for trust calibration)

Both regression commits carry the same model co-author trailer and exhibit the same wrong-execution-context mental model. The bug class is: assuming `post_install()` runs on the user's installed system when it actually runs inside the build chroot at package-install time. **The cross-pattern audit shape:**

```sh
grep -A 20 'post_install()' packages/*/*/build.sh | \
    grep -E 'keygen|generate|/etc/.*write|chpasswd|mkstemp|mktemp|first.boot'
```

Any post-install hook that does state-mutation against `/etc` or generates per-machine identity material — regardless of the package — is suspect for the same misread. Surfacing for operator review; not for autonomous fix.

### Trigger-case verdicts (concur with installed-system coordinator)

- **T0-2 shim path** → **Class A drift.** Concurs with installed-system coordinator's finding. Adds: commit history shows `977db9b3` (2026-05-03, "Path A" wiring) ratifying the Fedora-piggyback bootstrap arm; `docs/shim-review-submission.md` (637 lines) is in-tree evidence for the own-MS-shim arm. Both arms pre-authorized. Cycle-5 ISO wires neither.
- **T0-3 dracut vs PARTUUID** → **Class A drift.** Concurs. Adds: three Q-INIT commits (`77b0b453` + `cfcdc82e` + `a12216a5`, all 2026-05-06) explicitly rejected dracut-uefi: *"Q-INIT resolved 2026-05-05/06: April-10 custom-init stands. UKI wraps via systemd-stub + objcopy, NOT dracut --uefi."*
- **T0-4 SSH host-key baking** → **Class A drift.** Two-site regression-introduction forensics above. The directive PRE-DATES the explicit POWER RULE memory in Prime Directive + Holy Grail; the explicit memory codified what was already covered.

### Additional commit-history ratifications (complement IGOSC's tables)

| Category | Topic | Status | Provenance |
|---|---|---|---|
| BOOT | UKI envelope signing (not vmlinuz) | RATIFIED | `a12216a5` 2026-05-06 "phase5: pivot from vmlinuz signing to UKI signing" |
| BOOT | 3-UKI sealed-cmdline (live / install-gui / install-tui) | RATIFIED | Operator tracker 2026-05-14: *"Holy-Grail win on cmdline integrity"* |
| PACKAGE-MGR | Supersedes schema RFC v1 Phase 1 | RATIFIED | `4a73e16b` 2026-05-01 (4/4 PASS verdicts) |
| PACKAGE-MGR | S1/S2 canonical signing keys (NK1/NK2 alias-only) | RATIFIED | `c4c8ee02` 2026-05-12 |
| PACKAGE-MGR | Mirror promote: atomic directory-swap | RATIFIED | `5e998029` 2026-05-11 (owner-direct 23:46Z) |
| PACKAGE-MGR | Per-archive sig: v1.0 = NO, v1.1+ deferred | RATIFIED | `d6b3946a` 2026-05-12 (closure commit) |
| AI/INTERGEN | Voice descoped | RATIFIED | `docs/VISION.md:113` "Text interaction only — voice evaluated and dropped to keep the assistant simple and predictable" |

### Notes on vocabulary

Commit-message corpus uses "owner-direct" (~25 instances) consistently; "operator-direct" returns zero hits. Public-content audit gate should reconcile vocabulary to a single canonical term.

---

## Installed-system-coordinator findings

**Scan posture:** iter-1 initial scan via 5 parallel sub-agents (BOOT+PARTITION / SECURITY+LEGAL / PACKAGE-MGR+BUILD+DOCS/MIRROR / INSTALLER+DESKTOP/UX / AI/INTERGEN+PROCESS). ~150 findings across the 11-category taxonomy. Each row carries explicit provenance, status, and Class A-D classification. Iter-2 cross-check + iter-3 gap-pass to follow.

### Trigger-case verdicts (operator-named drift triggers)

- **T0-2 shim path** → **CLASS A drift**. Decision was RATIFIED 2026-04-18 as D1-7 piggyback ("MOK-until-MS-shim"); BOTH the own-MS-signed-shim arm AND the Fedora-piggyback bootstrap arm were pre-authorized in writing (`_archive/context_carryover_2026-04-18.md` L16+L75+L136, `docs/research/installer/ms_shim_sponsorship_2026-04-18.md`, `packages/core/shim-signed/package.yml:9-18` `pending_acquisition:` at `54da0926`, `docs/mok-enrollment.md:89` "target 2026-05-22 PR-open"). Cycle-5 ISO ships an InterGenOS-self-signed shim (audit B-001 HG) wiring NEITHER arm. The remediation plan's T0-2 framing of "acquire MS-signed shim OR ship Fedora-piggyback" as a fresh owner decision is incorrect — that decision was made; what is operationally open is the WIRING.
- **T0-3 dracut vs PARTUUID** → **CLASS A drift**. Decision was RATIFIED 2026-04-08/09 from empirical bare-metal evidence: PARTUUID + rootwait + no-installed-system-initramfs (`_archive/context_carryover_2026-04-09.md:24`, `docs/research/build_system/bare_metal_boot_issues_2026-04-08.md` §1, commits `27ce4ca9` + `c0454b2b`). Reinforced 2026-04-10 explicitly ("Custom /init script — LFS way. ~100 lines of shell + busybox. **No casper, no dracut, no mkinitcpio**" — `docs/research/installer/live_session_and_installer_FINAL_2026-04-10.md` §2) and 2026-05-06 (Q-INIT). `scripts/create-image.sh:148-150,177` correctly emits PARTUUID; `installer/backend/config.py:154-155` emits `root=UUID=$(blkid)` and diverges (audit B-026/B-041 Critical + B-028 policy-fork). The remediation plan's T0-3 framing of "Dracut vs PARTUUID-only no-initramfs path" as a fresh owner decision re-opens a settled question. Operationally-open is fixing one line in `config.py` to match.
- **T0-4 SSH host-key baking** → **CLASS A drift**. RATIFIED 2026-05-14 as a POWER RULE (`host-local memory entry`): "no dev keys, ever, in the shipped artifact"; credential injection at INSTALL TIME or FIRST-BOOT, never BUILD TIME. Every shipped ISO has identical SSH host keys (F-002/G-004 HG via `packages/core/openssh/build.sh:82` + `scripts/create-image.sh:280`) + `PermitRootLogin yes` (F-003 HG) + hardcoded `root:intergenos` password (F-004 HG via `packages/core/shadow/build.sh:70`, surviving the 2026-04-29 `7900c94` Path-4 retirement of the orchestrator default).

### Meta-finding

The POWER RULE `host-local memory entry` (ratified 2026-05-01) was itself violated by the remediation-plan synthesis pass — synthesis defaulted to memory-recall rather than provenance-probe. This matrix exists *because of* that drift. The remediation plan is a worked example of the failure mode the rule was codified to prevent. Recommend that synthesis pass cite the rule explicitly and add a future-state pre-condition: any "open" framing must show a memory-and-repo probe that found NO prior ratification.

### BOOT findings

| Category | Topic | Decision | Status | Date | Ratified by | Source | Conflicts | Class |
|---|---|---|---|---|---|---|---|---|
| BOOT | MOK-until-MS-shim path (D1-7) | Ship via Fedora-piggyback MS-signed shim as bootstrap; users boot via MOK-with-Fedora workflow; own MS-signed shim acquired in parallel via `rhboot/shim-review` (10-14 weeks). Both arms pre-authorized day-0. | RATIFIED | 2026-04-18 (initial); reaffirmed 2026-05-09 (`pending_acquisition`); reaffirmed 2026-05-11 | operator | `_archive/context_carryover_2026-04-18.md` L16+L75+L136; `docs/research/installer/ms_shim_sponsorship_2026-04-18.md`; `docs/shim-review-submission.md`; `docs/mok-enrollment.md:89`; `packages/core/shim-signed/package.yml:9-18` at `54da0926` | T0-2 owner-decision; B-001/A-001 (audit "open"); F-024; C-003 | B (re-ratification needed — decision RATIFIED but neither arm wired) |
| BOOT | shim-review submission target | PR-open 2026-05-22 against `rhboot/shim-review` (supersedes 2026-05-15). | RATIFIED | 2026-05-11 | operator | `docs/mok-enrollment.md:89`; `docs/shim-review-submission.md:4`; handoff.md L1123+L1448 | B-015 (PR-open slipped past 2026-05-15) | B |
| BOOT | Shim binary currently on shipped ISO | VIOLATES D1-7: cycle-5 ISO ships an InterGenOS-self-signed shim (CN=InterGenOS Secure Boot CA), NOT Fedora-piggyback; `shim-signed` package extracts Fedora binary but no orchestrator phase invokes it. | VIOLATED | observed 2026-05-18 audit | n/a | audit B-001 + A-001 (Holy-Grail); `installer/backend/bootloader.py:37-39`; `packages/core/shim-signed/package.yml` | D1-7 RATIFIED; T0-2 cluster | A (DRIFT) |
| BOOT | Signing-chain model | shim → GRUB (signed by InterGenOS distro EFI X.509 PIV slot 9c) → vmlinuz (signed) → in-tree .ko (ephemeral per-build key) → DKMS .ko (user MOK). | RATIFIED | 2026-05-05 (signing-ceremony close-out at `4a116d2`) | operator | `docs/mok-enrollment.md:26-50`; `host-local memory entry`; `docs/ceremony/signing-key-ceremony-procedure.md` | B-047 (doc says distro-EFI-signs vmlinuz; code MOK-signs at install); B-042 | B (doc/code drift on HOW vmlinuz gets signed) |
| BOOT | UKI / GRUB model | UKI (`vmlinuz` + initramfs + cmdline + os-release via `ukify build`, signed via sbsign) for live-ISO; GRUB-with-shim chainload for installed-system. | RATIFIED | 2026-05-06 (phase5 pivot at `a12216a`) | coordinator consensus + operator | handoff.md L1136; `scripts/build-uki.sh`; `installer/init/build-initramfs.sh:8` | E-001/E-002 HG (UKI doesn't prepend microcode initrd; cmdline ships KERN_DEBUG) | D (provenance only in handoff/commit; never in `docs/architecture/`) |
| BOOT | Initramfs build technique (LIVE ISO) | Custom 100-line `/init` + statically-linked busybox. NO casper, NO dracut, NO mkinitcpio. Required modules: squashfs/overlay/loop/isofs/vfat/ext4. | RATIFIED | 2026-04-10 (FINAL); reaffirmed 2026-05-05/06 (Q-INIT) | operator | `docs/research/installer/live_session_and_installer_FINAL_2026-04-10.md` §2 + §5; `reorientation_prompt_2026-05-06_evening.md:68`; `installer/init/build-initramfs.sh:8` | T0-3 remediation framing doesn't distinguish live-ISO custom-init from installed-system question | C (live-ISO RATIFIED; remediation scope conflates) |
| BOOT | Initramfs for INSTALLED system | NO installed-system initramfs generator. v1.0 ships without dracut/mkinitcpio in chroot; root-mount via PARTUUID + kernel-builtin storage drivers + `rootwait`. | RATIFIED (implicit) | 2026-04-08 (bare-metal validation); commits `27ce4ca9` + `c0454b2b` | operator | `_archive/context_carryover_2026-04-09.md:24`; `docs/research/build_system/bare_metal_boot_issues_2026-04-08.md` §1; `scripts/create-image.sh:148-150` | B-026/B-041 (Critical: Forge install has no initramfs gen + `root=UUID=`); B-028 (policy-fork); T0-3 framing as owner-decision | A (DRIFT — `installer/backend/config.py:154-155` emits UUID= contradicting ratified PARTUUID-only) |
| BOOT | Microcode early-load | Intel microcode loaded via `iucode_tool` early-load cpio at `/boot/intel-ucode.img`. Validated on bare metal April 9. | RATIFIED | 2026-04-09 | operator | `_archive/context_carryover_2026-04-09.md:25,40-41`; commit `67bc6dd1` | F-001/E-001/E-002 (HG triple-failure — never loads cycle-5; ukify single `--initrd=` skips); T0-1 | A (DRIFT — ratified path never reaches the UKI) |
| BOOT | MOK strategy | One MOK per machine, generated by Forge at install, queued via `mokutil --import`, enrolled via shim MokManager at first boot; key at `/var/lib/intergen/mok/mok.key`; signs DKMS / out-of-tree only. NOT a substitute for FDE. | RATIFIED | 2026-04-18 (Forge SB Monday-cut); reaffirmed 2026-05-11 | operator | `_archive/context_carryover_2026-04-18.md` L14+L73; `docs/mok-enrollment.md` §1; `installer/backend/mok.py`; handoff.md L40-44 | B-042/B-043; F-013 (MOK TPM sealing) — Phase A decision #7 | D (RATIFIED; only F-013 TPM-sealing arm open) |
| BOOT | Measured-boot scope (TPM) | Out of scope for v1.0. TPM 2.0 errors are VM-only benign; systemd-pcrlock kernel-prereq is hardening followup; FDE/TPM sealing of MOK is v1.x. | DEFERRED | 2026-05-15 (P7 parked) | operator | handoff.md L207; audit Lane E hardening list; F-013 | F-013 (v1.0 stance owed) | D |
| BOOT | SBAT generation | 6-line SBAT block adopted: `shim,4 / shim.intergenos,1 / grub,5 / grub.intergenos,1 / linux,1`. Q-SBAT ratified. | RATIFIED | 2026-05-06 | operator | `context_carryover_2026-05-06.md:63`; handoff.md L1140; `packages/core/grub/sbat.csv` | F-028 (sbat.csv ships only 3 of 6 ratified entries) | A (DRIFT in sbat.csv content) |
| BOOT | Pre-signing UX (Pattern A) | Pattern A interim: MokManager + secure-boot-quickstart.md doc for users at first boot. Q-PRESIGN-UX=YES. | RATIFIED | 2026-05-06 | operator | `context_carryover_2026-05-06.md:63`; handoff.md L1146 | B-043 (MokManager has no InterGenOS UX choreography) | A |

### PARTITION findings

| Category | Topic | Decision | Status | Date | Ratified by | Source | Conflicts | Class |
|---|---|---|---|---|---|---|---|---|
| PARTITION | Default disk strategy | Plain GPT + ESP (512MB FAT32) + root (rest, ext4). BIOS: bios_grub (1MB) + root. NO LVM, NO LUKS, NO btrfs/ZFS at v1.0. Optional swap. | RATIFIED | 2026-04-05; 2026-04-10 (FINAL); 2026-04-18 | operator | `docs/research/installer/installer_design_plan_2026-04-05.md:67-72`; `docs/research/installer/live_session_and_installer_FINAL_2026-04-10.md:368`; `installer/backend/disks.py:125-126` | none — matches code | D (RATIFIED + in-code; never in `docs/architecture/`) |
| PARTITION | Alongside-install scope | Alongside-shrink for Windows-NTFS (not BitLocker-encrypted). Primitives: `detect_shrinkable_ntfs`, `is_bitlocker_encrypted`, `partition_disk_alongside`. NVMe-replace not v1.0. | RATIFIED | 2026-04-18 (Q5 + BitLocker approach owner-greenlit) | operator | `_archive/context_carryover_2026-04-18.md:75,135`; `installer/backend/disks.py:194-298` | C-014 (remediation re-opens as "keep+complete OR delete v1.x") | B (RATIFIED is keep+complete; remediation re-opens) |
| PARTITION | LUKS / LVM / BTRFS / ZFS / FDE | NOT v1.0. Documented "Future" / Phase 2. Live initramfs explicitly lacks cryptsetup + LVM tooling. | DEFERRED | 2026-04-05 | operator | `docs/research/installer/installer_design_plan_2026-04-05.md:72,250,253`; audit B-033, E-041 | none — matches | D |
| PARTITION | Swap default | 2GB swap supported (bare-metal April 9); not default-on. | RATIFIED (implicit) | 2026-04-09 | operator | `_archive/context_carryover_2026-04-09.md:44`; `installer/backend/disks.py:125-157` (no default swap) | none | C (no explicit trail in memory) |
| PARTITION | Root filesystem | ext4 (label `intergenos`). Single root partition. No subvolumes. | RATIFIED | 2026-04-05 / 2026-04-10 | operator | `installer_design_plan_2026-04-05.md:70`; `installer/backend/disks.py:145,157,329`; `scripts/create-image.sh:158` | none | D |
| PARTITION | ESP umask hardening | ESP mounted `umask=0077` (hides signing-key staging from non-root). | RATIFIED | 2026-04-18 (Forge SB Monday-cut) | operator | `installer/backend/config.py:27-32` + L32 comment; `_archive/context_carryover_2026-04-18.md:73` | none | D |
| PARTITION | os-prober posture | `GRUB_DISABLE_OS_PROBER=false` for Forge installs (dual-boot menu support). Diverges from `create-image.sh` QCOW2 path which sets `=true`. | RATIFIED for Forge | 2026-04-18 | operator | `installer/backend/config.py:160-168`; comment L158-159 | B-028 (audit High — divergent policies Forge vs create-image.sh) | B (both RATIFIED for different ISO types; audit correctly flags fork) |
| PARTITION | USB-stick disk filter | Disk-screen MUST exclude live-USB boot device (detect via `/proc/cmdline BOOT_IMAGE=`). | PROPOSED | 2026-05-18 (audit) | n/a | audit N-013 (iter-3 new); C-012 sibling | N-013 open | C |

### SECURITY findings

| Category | Topic | Decision | Status | Date | Ratified by | Source | Conflicts | Class |
|---|---|---|---|---|---|---|---|---|
| SECURITY | SSH host-key baking (build-time) | Pre-installed SSH pubkeys, certificates, API tokens, or any credentials MUST NOT ship in public ISO. Inject at INSTALL TIME (operator) or FIRST-BOOT (greeter), never BUILD TIME. Cluster-wide. | RATIFIED | 2026-05-14 | operator (mid-Wave-C.1 correction before any commit landed) | host-local POWER RULE archive (POWER RULE) lines 9-33 | F-002, F-008, G-004 (`packages/core/openssh/build.sh:82`, `scripts/create-image.sh:280`); T0-4 sub-cluster 1 | A |
| SECURITY | Holy Grail — Security-Only Alignment | "Security ONLY" supersedes Prime Directive in security matters. 10 rules (default-deny, credentials sacred, Secure Boot mandatory, defense in depth, etc.) | RATIFIED (SUPREME) | ~2026-03 (AI-proliferation reality doctrine) | operator | host-local doctrine archive | G-005 (Rule 10); F-002/F-003/F-004/G-004 (Rule 8); F-038/F-039 (Rule 5) | A |
| SECURITY | Security-review filter for build decisions | Every build decision passes security review. STOP / RESEARCH / PROPOSE / WAIT. | RATIFIED | 2026-04-10 | operator (RULE #4) | host-local memory entry | doctrinal filter for all SECURITY rows | — |
| SECURITY | Hardcoded `root:intergenos` literal retired | Build-time hardcoded default permanently retired. `--root-password` and `--user-password` are required orchestrator flags; first-boot greeter is canonical end-user credential-set. | RATIFIED | 2026-04-29 21:51Z; greenlit 22:04:01Z | operator + coordinator consensus (Path A + 3b unanimous) | `memory/context_carryover_2026-04-29_evening.md:47,72`; commit `7900c94`; `docs/operations/02-running-the-builder.md:37` | F-004 (`packages/core/shadow/build.sh:70` still bakes `intergenos` password — package-level residual not stripped by Path 4 fix) | A |
| SECURITY | First-boot password greeter (tty path) | tty greeter (not Plymouth) prompts for root + user passwords at first boot. systemd Type=oneshot, Conflicts=getty@tty1, ConditionPathExists guard. | RATIFIED | 2026-04-29 (Path A+3b unanimous) | operator + coordinator consensus | `context_carryover_2026-04-29_evening.md:47-52`; merged at `89b9605`; `docs/operations/02-running-the-builder.md:37` | F-006 (tty2 root autologin in live mode bypasses); D-001/D-002 (greeter binary stub-shape) | A |
| SECURITY | AppArmor as primary LSM | AppArmor major LSM. Profiles in **enforce** mode for daemon fleet. `DEFAULT_SECURITY="apparmor"`. | RATIFIED | 2026-04-29 20:28Z (4-0 unanimous; greenlit 20:45:33Z) | operator + coordinator consensus | `context_carryover_2026-04-29_evening.md:41`; commit `2f952b5`; `docs/users/security-defaults.md:14` | F-039 (profiles ship but orchestrator never transitions — defense theater); F-038 (intergen.service no hardening); F-048/F-049 | A |
| SECURITY | Aggressive systemd hardening baseline for daemons | NoNewPrivileges, ProtectSystem=strict, ProtectHome, PrivateTmp/Devices, ProtectKernel*, RestrictAddressFamilies, RestrictNamespaces, MemoryDenyWriteExecute, SystemCallFilter=@system-service ~@privileged @resources @mount @swap @reboot. | RATIFIED | published v1.0 doctrine | operator | `docs/users/security-defaults.md:15-37` | F-038 intergen.service runs root with ZERO hardening (less than nginx); F-048/F-049 | A |
| SECURITY | "Safe Network Binds" — services localhost-only by default | Server packages bind to 127.0.0.1 by default; never listen on public interfaces unless deliberately edited. | RATIFIED | published v1.0 doctrine | operator | `docs/users/security-defaults.md:38` | F-003/F-009/F-032 sshd contradicts; G-005 firewall policy=accept | A |
| SECURITY | "No Default Passwords" claim | "We do not ship databases or services with blank or default 'admin' passwords." | RATIFIED | published v1.0 doctrine | operator | `docs/users/security-defaults.md:39` | F-004 hardcoded `root:intergenos` directly contradicts | A |
| SECURITY | Kernel lockdown LSM | `CONFIG_SECURITY_LOCKDOWN_LSM=y` + EARLY; integrity-mode auto-promotion under SB. | RATIFIED | 2026-05-15 | build-system coordinator (doc) | `docs/shim-review-submission.md:254,269,570` | E-013 / F-043 DEVMEM/PROC_KCORE/DEVPORT still set | C/A |
| SECURITY | Secure Boot mandatory | Mandatory MS-signed shim → InterGenOS GRUB → kernel chain; signed kernel modules; MOK enrollment guided by Forge. | RATIFIED | Holy Grail Rule 3 | operator | host-local doctrine archive; `docs/users/security-defaults.md:13` | T0-2 items are MOK UX gaps, not policy drift | — |
| SECURITY | No-stub / "stub is a lie" | Code that announces an action MUST perform it; never commit feature-disable as fix. | RATIFIED | 2026-05-15 (Rule 21) | operator | `docs/build-development-rulebook.md:298`; host-local POWER RULE archive | F-038/F-039 (defense theater); G-039 (oomd inert); I-027/I-029 (safety.py never invoked) | A |
| SECURITY | Audit-finding "hardening" bulk-apply | FORBIDDEN. Default disposition = REVIEW, not APPLY. Per-finding operator ratification required. | RATIFIED | 2026-05-08 ~20:12Z | operator (verbatim) | host-local memory entry | governs HOW T0-4/T1-2/T2-2 hardening is dispatched | — |
| SECURITY | nftables service shipping | `core/nftables` ships service unit + nftables.conf default ruleset; release 2→3 at `794df73a`. | RATIFIED | 2026-05-14 ~19:50Z | operator (theming-marathon dispatch) | `context_carryover_2026-05-14_theming_marathon_arc.md:162-163,343`; commit `794df73a` | G-005 (shipped policy=accept is HG-violation per Rule 10); F-035 (install-theming.sh writes second policy=drop conf that wins, allows tcp/22) | A (G-005) + A (F-035 drift on canonical service) |
| SECURITY | sshd default posture | (NO prior owner ratification found of `PermitRootLogin yes` or sshd-enabled-by-default; documented user-facing posture says "localhost-only") | UNKNOWN ratification; VIOLATED doctrine | — | — | absence; contradicted by `docs/users/security-defaults.md:38-39` | F-003, F-009, F-016, F-032, G-021; T0-4 sub-cluster 2; Phase A no-blocker | A |
| SECURITY | PAM password policy / faillock | (No prior owner ratification; libpwquality builds but never wired into PAM) | UNKNOWN ratification | — | — | `packages/core/libpwquality/` builds; `packages/core/linux-pam/build.sh:55-107` doesn't ship pwquality/faillock | F-010, C-050, C-051; T0-4 sub-cluster 3; Phase A decision #8 (F-023) | C |
| SECURITY | Live ISO tty2 root-autologin | (No prior owner ratification; "emergency fallback" code-only) | UNKNOWN; conflicts with no-default-passwords | — | — | `installer/init/init.sh:308-315`; no memory provenance | F-006 (HG); contradicts no-default-passwords | A (drift from doctrinal posture) |
| SECURITY | CVE-2026-31431 (algif_aead) mitigation | Module blacklist + kernel patch. | RATIFIED | 2026-04-29 | operator | `context_carryover_2026-04-29_evening.md:56`; `SECURITY.md:124-126`; `docs/security/advisories/CVE-2026-31431-copy-fail.md` | F-018 (incomplete mitigation; T2-2) | B |
| SECURITY | NOPASSWD wheel-sudo on installed system | (Operational docs prescribe NOPASSWD on build VM; NO security-doctrine ratification of NOPASSWD on installed default user) | UNKNOWN ratification | — | — | `docs/operations/01-build-vm-setup.md:12,154-155`; `docs/operations/07-golden-builder-snapshot.md:12` | I-035 (manage_services auto-sudo + passwordless wheel = LLM root-equivalence); F-042; T0-4 sub-cluster 8 | A |
| SECURITY | Hibernation / RAM-disclosure surfaces | (No prior memory ratification; KSPP-aligned remediation proposed) | UNKNOWN; conflicts with Holy Grail Rule 5 | — | — | absence; `config/kernel/fragments/00-universal-baseline.config:2237,1378,2557` | E-011, E-013, F-043 (Critical); F-041; T2-4 | C |
| SECURITY | LUKS / FDE at install time | No design doc, no future-wiring stub, no installer code. Holy Grail Rule 8 implies data-at-rest is not trivially extractable. | UNKNOWN / DEFERRED to v1.x | — | — | absence; `installer/backend/disks.py` | N-018; Phase B decision #32 | C |
| SECURITY | "No Telemetry, No Auto-Updates, No Opt-Out Privacy" | Zero analytics/crash reports/usage stats. Updates only on explicit `pkm update`. | RATIFIED | published v1.0 doctrine | operator | `docs/users/security-defaults.md:57-64` | P-020 PRIVACY.md absent (LEGAL/GDPR surface gap) | — |
| SECURITY | DKMS auto-signing / signed kernel modules | "We do not trust unsigned kernel modules." Forge walks MOK enrollment. | RATIFIED | published v1.0 doctrine; Holy Grail Rule 3 | operator | `docs/users/security-defaults.md:13`; host-local doctrine archive | T0-2 step 8 MOK UX closes the loop; F-013/B-050 (Phase A decision #7) | B |

### LEGAL findings

| Category | Topic | Decision | Status | Date | Ratified by | Source | Conflicts | Class |
|---|---|---|---|---|---|---|---|---|
| LEGAL | Repo license | GNU GPL-3 (root `LICENSE` is GPL-3 verbatim). | RATIFIED | repo inception | operator | `/LICENSE:1-2` | P-017 brand assets fall under GPL-3 default — competitor could fork+rebrand+malware | A (downstream legal gap) |
| LEGAL | LFS / BLFS attribution | LFS 13.0 + BLFS 13.0 (systemd) under CC-BY-NC-SA 2.0. | RATIFIED in `CREDITS`; drifted | repo inception | operator | `/CREDITS:6-30` | P-002 — NC clause incompatible with commercial distribution; LFS book is CC-BY-NC-SA 3.0 today | B (version drift) |
| LEGAL | SBOMs — SPDX 2.3 JSON | "InterGenOS emits SPDX 2.3 JSON SBOMs detailing exactly what dependencies and source hashes comprise our critical system binaries." | RATIFIED | published v1.0 doctrine | operator | `docs/users/security-defaults.md:55`; `docs/sboms/intergenos-shim-x64-20260515.spdx.json` (shim SBOM only) | P-007 (zero SPDX headers in 228 InterGenOS-authored source files; SBOM exists only for shim) | A (claim wider than evidence) |
| LEGAL | Security disclosure policy + PGP | RFC 9116 security.txt; PGP-encrypted submissions; advisories with CVE/CVSS/patch; Project-Zero-style 90-day disclosure. | RATIFIED | 2026-05-12 | operator | `/SECURITY.md:1-130` | — | — |
| LEGAL | Signed binary mirror posture | End-to-end signed index; signature against offline-generated master release key; SHA-256 file verification; index-only signature trust for v1.0. | RATIFIED in user-facing doc | published v1.0 doctrine | operator | `docs/users/security-defaults.md:7,43-45` | L-001 — zero packages ever signed/published; `first-publish-runbook.md:4` "never been exercised end-to-end"; P-001 no GPL §6 source-availability | A + A |
| LEGAL | Zero-PyPI for critical Python deps | Critical Python deps from verified GitHub release tags, not PyPI. Canonical: `packages/core/maturin/`. | RATIFIED | published doctrine (hardened during 2026-05-11/12 PyPI Mini Shai-Hulud window) | operator | `docs/users/security-defaults.md:53`; `memory/handoff.md:717` | — | — |
| LEGAL | Reproducible cargo-vendor / Go pipelines | Rust + Go use reproducible offline-vendor pipeline; helper landed 2026-05-12. | RATIFIED | 2026-05-12 | operator | `docs/users/security-defaults.md:54`; `fbe69af4` and earlier | — | — |
| LEGAL | "No Proprietary Firmware in Core" | Core OS open-source drivers/firmware; proprietary blobs available but never forced. | RATIFIED | published v1.0 doctrine | operator | `docs/users/security-defaults.md:64` | P-003 FDK-AAC in `desktop` tier without opt-in; P-006 helper mislabel as GPL-3 | A |
| LEGAL | GPL source-availability mechanism (GPLv3 §6) | (No prior ratification of §6b written offer or §6d network access; mirror designed in `docs/mirror/design.md` but never live; no `SOURCES.md`, no `COPYING.offer`) | UNKNOWN ratification | — | — | absence; `docs/mirror/design.md`; `docs/research/build_system/vps_source_mirror_design_2026-04-02.md:12`; `README.md:163` | P-001 (HG) textbook GPL violation on first redistribution; Phase B decision #27 | C |
| LEGAL | FFmpeg licensing | `packages/desktop/ffmpeg/package.yml:5` declares `license: LGPL-2.1-or-later` | RATIFIED metadata; VIOLATED in build | repo (date unknown) | repo state | `package.yml:5` vs `build.sh:13-33` (`--enable-nonfree --enable-libfdk-aac`) | P-015 (HG v1.0 ship-blocker) — binary non-redistributable, license metadata impossible | A |
| LEGAL | Default LLM model licensing (Qwen 3.5) | `intergen/model_manager.py:30-58` auto-fetches Qwen3.5-2B/9B/35B-A3B; intergen package.yml declares only GPL-3 for wrapper. | RATIFIED in code; LEGAL drift | repo state | repo (no explicit owner ratification found) | `intergen/model_manager.py:30,32,42,52`; `packages/ai/intergen/package.yml:5`; `docs/architecture.md:89-92`; `docs/VISION.md:104-106` | P-016 (HG v1.0 ship-blocker) — Tongyi Qianwen License: 100M-MAU clause, "Powered by Qwen" attribution §4, use-restrictions; no license bundle, no acceptance gate, no attribution surface; Phase B decision #18 | A |
| LEGAL | Trademark / brand-asset license carve-out | (No `TRADEMARK.md`; no carve-out of `assets/intergen-mark/` from root GPL-3) | UNKNOWN ratification | — | — | absence; grep "trademark" returns zero outside design-research docs | P-017 Critical — competitor forks + rebrands + ships malware, operator has no recourse on the brand | C |
| LEGAL | DCO / Signed-off-by trailer | (Not enforced; no `DCO.md`; CONTRIBUTING.md doesn't require Signed-off-by) | UNKNOWN ratification | — | — | `/CONTRIBUTING.md`; absence | P-018; pre-push gate 10; T0-6 sub-item | C |
| LEGAL | BIS / EAR / EU dual-use encryption export notice | (Not present; ships openssl, gnutls, libgcrypt, nss, nettle, gpgme, gnupg2, cryptsetup; no `docs/legal/EXPORT-NOTICE.md`) | UNKNOWN ratification | — | — | absence | P-024 — 5D002 ENC TSU classification; embargoed jurisdictions | C |
| LEGAL | PRIVACY.md / GDPR Art. 13-14 + CCPA §1798.130 | (Not present; "no telemetry" posture in security-defaults but no privacy-policy surface) | UNKNOWN ratification | — | — | absence | P-020; T0-6 — root + `/usr/share/doc/intergenos/PRIVACY` + firstboot greeter link | C |
| LEGAL | License-policy governance doc | (`docs/governance/license-policy.md` doesn't exist; only `succession.md`) | UNKNOWN ratification | — | — | `ls docs/governance/` shows only `succession.md` | P-005 AGPL ghostscript/mupdf compliance not audited | C |
| LEGAL | "Cannot legally redistribute, but can make effortless to install" pattern | Helper-package pattern: install scripts under GPL-3; proprietary downstream not redistributed; user makes the choice. Canonical: VS Code / Claude Code. | RATIFIED (design pattern) | 2026-04-05 | operator | `docs/research/vscode_claude_code_proposal_2026-04-05.md:133` | P-006 — helper packages currently mislabel proprietary downstream as GPL-3 (no `payload_license:` field, no EULA acceptance) | A (drift from pattern) |
| LEGAL | Per-package LICENSE bundling / THIRD-PARTY-NOTICES | (Not present; no build-time extraction of upstream LICENSE/COPYING into `/usr/share/licenses/<pkg>/LICENSE`) | UNKNOWN ratification | — | — | absence | P-004, P-010, P-014, P-019, P-021, P-022, P-026 | C |
| LEGAL | Hall of Fame reporter credit | Accepted reports credited in advisory + Hall of Fame unless anonymity requested. | RATIFIED | 2026-05-12 | operator | `/SECURITY.md:92-97`; `docs/hall-of-fame.md` | — | — |

### PACKAGE-MGR findings

| Category | Topic | Decision | Status | Date | Ratified by | Source | Conflicts | Class |
|---|---|---|---|---|---|---|---|---|
| PACKAGE-MGR | pkm trust model (signed-index-only vs per-archive) | v1.0 ships signed-index-only; per-archive `.sig` deferred to v1.1+ with documented trigger conditions. | RATIFIED | 2026-05-12 | individual coordinator (build-system lane) | `docs/architecture/per-archive-sig-decision.md:5,9`; `docs/operational/first-publish-runbook.md:92,388` | L-007 (`docs/mirror/design.md` §2 still lists `.tar.gz.sig` in layout); plan owner-decision #24 wrongly reopens | B (runbook RATIFIED; design.md + apache snippet + mirror-publish.sh still aspirational) |
| PACKAGE-MGR | GPG trust anchor / signing-key topology | Master `5597A3E0…83050` certifies; 4 signing subkeys [S1-S4] on Nitrokeys do release-signing; master never signs release artifacts. | RATIFIED | 2026-05-05 (ceremony close-out) | operator | host-local reference memory; `docs/signing-key.md` | L-002 — `scripts/mirror-publish.sh:23` hardcodes master fingerprint as `GPG_KEY_FP`; `docs/mirror/design.md:147` repeats wrong fingerprint | A (drift; collapses offline-root posture) + B (vs `publish-repo.sh` which uses correct [S1]) |
| PACKAGE-MGR | GPG keyring shipped on installed system | `/etc/pkm/trusted.gpg` must ship in chroot with release public key. | RATIFIED in code-expectation; never landed | 2026-05-12 | individual coordinator (build-system lane) | `pkm/repo.py:74` `GPG_KEYRING = '/etc/pkm/trusted.gpg'`; `docs/operational/first-publish-runbook.md` trust-chain section | H-001 / L-008 / A-031 — keyring never created in `packages/core/pkm/build.sh` | C |
| PACKAGE-MGR | repos.conf shipped config format | INI format with `[intergenos-current]` stanza | VIOLATED | (build.sh write date) | (no recorded ratification) | `packages/core/pkm/build.sh:35-46` writes INI; `pkm/repo.py:148` parses `json.load()` | H-002 / L-015 / O-004 — silent fallback to `DEFAULT_REPOS` | D (in repo but no provenance; needs operator ratification of INI-vs-JSON) |
| PACKAGE-MGR | Anti-rollback / replay defense | (no decision recorded) | UNKNOWN | — | — | absent from `docs/mirror/design.md`, runbook, memory | L-019 — no `last_seen_generated` monotonic check, no `INDEX_MAX_AGE` | C |
| PACKAGE-MGR | Schema-version refusal / sig-algorithm-agility | (no decision recorded) | UNKNOWN | — | — | absent | L-020 | C |
| PACKAGE-MGR | Upgrade safety semantics (`pkm upgrade linux-kernel`) | (no decision recorded; current code is remove-first-then-install) | VIOLATED-by-default | — | — | `pkm/cli.py` cmd_upgrade — `remover.remove(force=True)` BEFORE installing new | O-002 / O-007 — partial failure = unbootable; "atomic supersedes" claim contradicts | A (drift from "no known issues" / "fix-it-don't-defer") |
| PACKAGE-MGR | post_install hook execution on upgrade | (no decision recorded) | UNKNOWN / VIOLATED | — | — | hooks never fire on `pkm upgrade` | O-003 | C |
| PACKAGE-MGR | `pkm sync` subcommand existence | "pkm sync" used pervasively in user docs as day-one command | VIOLATED | — | — | K-017 — subcommand is `update`, not `sync` | K-017 / O-012 / O-019 / O-020 | B (docs ratified one name, CLI ships another; owner-decision #28) |
| PACKAGE-MGR | Tar extraction path-traversal protection | (no decision recorded) | UNKNOWN | — | — | uses subprocess `tar` no `--no-overwrite-dir`/`--anchored` | H-022 (HG) | C |
| PACKAGE-MGR | Process-level lock on pkm DB writes | (no decision recorded) | UNKNOWN | — | — | concurrent pkm invocations race on FS deploy | H-023 | C |
| PACKAGE-MGR | release-keys.json coverage of S1-S4 | All four signing subkeys are in policy; release-keys.json must enumerate all. | RATIFIED-in-memory (HW); VIOLATED-in-software | 2026-05-05 | operator (ceremony output) | `host-local memory entry` topology table lists [S1-S4]; `pkm/release-keys.json` only contains S1+S2 | L-027 | C |

### BUILD findings

| Category | Topic | Decision | Status | Date | Ratified by | Source | Conflicts | Class |
|---|---|---|---|---|---|---|---|---|
| BUILD | Reproducible builds goal | `Q-REPRO-GOAL=v1.0 bit-identical`; v1.x byte-identical `.igos.tar.gz` per package; bootstrap-layer/ISO determinism deferred. | PROPOSED (scoping/pre-impl) | 2026-05-12 | individual coordinator (build-system lane) | `docs/architecture/reproducible-builds-design.md:3,9-23`; `scripts/build-intergenos.sh:981` | A-012 / A-025 / A-044 — 3 sites still use non-reproducible `tar -czf` | C (design captured, fixes not applied) |
| BUILD | `SOURCE_DATE_EPOCH` discipline for canonical-bundled tarballs | MANDATORY POWER RULE: bundle from canonical repo path with `SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct .)` + `tar --sort=name --owner=0 --group=0 --numeric-owner --mtime=@SDE`. | RATIFIED | 2026-05-14 | operator (correction at `004a215a`) | host-local POWER RULE archive | A-012 / A-025 / A-044 — `scripts/pkg-functions.sh:420`, `igos-build/tracker.py:151`, `:756` all use plain `tar -czf` | A (drift from POWER RULE; 3 sites violate) |
| BUILD | cargo-vendor reproducible recipe | Canonical recipe established + working. | RATIFIED-and-WORKING | (cargo-vendor work) | individual coordinator (build-system lane) | `scripts/cargo-vendor-gen.sh`; `docs/architecture/reproducible-builds-design.md:166-177` | none | D |
| BUILD | pax-format tar (long-path-safe) | pax-format selected over ustar for long crate paths. | RATIFIED | (master `719db93e`) | individual coordinator (build-system lane) | `docs/architecture/reproducible-builds-design.md:54` | none | D |
| BUILD | Signing ceremony automation scope | `scripts/ceremony/ceremony.py` (2213 LOC, PII-sanitized) = canonical ceremony automation; ran 2026-05-05 ceremony. | RATIFIED | 2026-05-05 (operational); 2026-05-06 (publish at `4a116d23`) | operator | `host-local memory entry`; `docs/ceremony/signing-key-ceremony-procedure.md` | B-022 — ceremony.py untested at unit level | D |
| BUILD | Build environment — chroot only, never live target | ALL packages built in chroot on Ubuntu build VM; target system is disk-image from chroot. | RATIFIED | 2026-04-02 | operator (post glib2 bricking) | host-local project memory | none in scope | (reference) |
| BUILD | No dev keys / SSH keys / credentials in shipped ISO | POWER RULE: build-time credential bake FORBIDDEN; install-time / first-boot only. | RATIFIED | 2026-05-14 | operator | host-local POWER RULE archive | F-002 / F-003 / F-004 / G-004 / G-021 — every shipped ISO has identical SSH host keys + hardcoded `root:intergenos`. T0-4 explicit drift case. | A |
| BUILD | Orchestrator scope (phase_squashfs + phase_iso wiring) | Orchestrator should produce bootable signed ISO end-to-end via `scripts/build-intergenos.sh` with no manual steps. | PROPOSED/VIOLATED | — | (implicit) | `docs/operations/02-running-the-builder.md` declares canonical 17-phase order; orchestrator skips phase_squashfs + phase_iso | A-002 / T1-1 — 80+ ad-hoc kickoff scripts orbit the orchestrator | C |
| BUILD | Test framework / pytest canonicalization | pytest with `[tool.pytest.ini_options] testpaths`; coverage 60% v1.0 / 80% v2.0; smoke `--release` turns SKIPs into FAILs. | PROPOSED | 2026-05-18 (synthesis) | coordinator consensus (audit synthesis) | `docs/audit/2026-05-18-remediation-plan.md:303-311` T1-4 | M-012..M-056 — no canonical pytest config, no coverage floor | D |
| BUILD | "Tests define behavior — fix code, not tests" | POWER RULE | RATIFIED | 2026-04-14 | operator | host-local POWER RULE archive | none directly | (reference) |
| BUILD | Audit-finding "hardening" bulk-apply discipline | FORBIDDEN; default = REVIEW not APPLY; per-finding operator ratification. | RATIFIED | 2026-05-08 | operator (verbatim) | host-local POWER RULE archive | (governs remediation-plan apply procedure itself) | (reference) |
| BUILD | BLFS database governance | Authoritative `blfs-packages.db` lives on Ubuntu host; sync before use; never regenerate locally. | RATIFIED | — | individual coordinator (build-system lane) | host-local reference memory | none | (reference) |
| BUILD | Non-reproducible tar at 3 sites | (violates RATIFIED `feedback_bundle_from_canonical_not_installed`) | VIOLATED | — | — | `scripts/pkg-functions.sh:420`, `igos-build/tracker.py:151`, `:756` plain `tar -czf` | A-012 / A-025 / A-044 (T3-3) | A |

### DOCS/MIRROR findings

| Category | Topic | Decision | Status | Date | Ratified by | Source | Conflicts | Class |
|---|---|---|---|---|---|---|---|---|
| DOCS/MIRROR | Canonical research home | Ubuntu workstation `~/intergenos/research/` is the shared research substrate; promotion to other-host local home dirs = invisible. | RATIFIED | 2026-05-06 | individual coordinator (build-system lane) per operator-direct | host-local POWER RULE archive | none in scope | (reference) |
| DOCS/MIRROR | Durable working files location | `~/tmp/` on every host; NEVER `/tmp/` (tmpfs, volatile). | RATIFIED | 2026-05-04 | operator (correction #3) | host-local POWER RULE archive | none | (reference) |
| DOCS/MIRROR | VPS administrative ownership | Build-system coordinator manages `origin.intergenstudios.com` (mail config, forwarders, mirror layout, MCP routes, session log, sources tree). | RATIFIED | 2026-05-05 | operator (direct) | host-local POWER RULE archive | none | (reference) |
| DOCS/MIRROR | Mirror VPS / hostname / path | (3 incompatible designs in tree) | CONFLICTED | — | — | `scripts/publish-repo.sh` → `repo.intergenos.org:/home/intergenos/repo/x86_64`; `scripts/mirror-publish.sh` + `docs/mirror/design.md` → `intergenstudios.com/mirror/x86_64`; `docs/operational/first-publish-runbook.md:59-66` → `intergenos@origin.intergenstudios.com:2200:/home/intergenos/repo/x86_64/` | L-005 (HG); owner-decision #23 still listed open | B (three concurrent design records; never reconciled) |
| DOCS/MIRROR | Mirror design canonical doc | `docs/mirror/design.md` v1.0 (commit `ea6fdad2` 2026-05-16) | PROPOSED ("awaiting owner ratification before any VPS-side file lands") | 2026-05-16 | (none yet) | `docs/mirror/design.md:3` | L-005, L-006, L-009 | D |
| DOCS/MIRROR | Public hosting plan | `intergenos.org` + `repo.intergenos.org` as two public domains; static HTML on .org; mirror API-only on repo.*. | PROPOSED (TODO scaffold) | 2026-05-12 | (none yet — listed Owner) | `docs/architecture/public-hosting-plan.md:1-7,84` | DIRECTLY conflicts with mirror/design.md path-mount approach on `intergenstudios.com/mirror/` | B |
| DOCS/MIRROR | Runbook canon (operations runbook 10-topic set) | `docs/operations/01-..10-` canonical operator runbook set 2026-05-15; companion `docs/operational/first-publish-runbook.md` is "v0.1 pre-flight draft, never exercised end-to-end". | RATIFIED-as-doc / NEVER-EXERCISED | 2026-05-15 | individual coordinator (build-system lane); promoted to MCP reference resources silently | `docs/operations/README.md`; `7f58b264` + `2eb4d873` + `b402b498` + `7a0d5f19`; `docs/operational/first-publish-runbook.md:4` | L-001 (HG) "signed mirror is theatre" | C |
| DOCS/MIRROR | Doc layout convention (research/ vs docs/ vs assets/) | `docs/research/` (research artifacts), `docs/architecture/` (architecture records), `docs/operations/` (operational runbooks), `docs/operational/` (first-publish-runbook — different subdir!). | RATIFIED-but-INCONSISTENT | various | individual coordinator (build-system lane) | both `docs/operations/` + `docs/operational/` exist | (internal naming inconsistency) | B |
| DOCS/MIRROR | atomic-promote shape (mirror) | directory-rename atomic preferred over symlink-swap. | RATIFIED-in-doc; VIOLATED-in-other-script | 2026-05-16 | individual coordinator (build-system lane) per design.md | `docs/mirror/design.md:67-72`; `scripts/publish-repo.sh:141-145,172-184` (claims dir-swap, implements symlink-swap) | L-009, L-017 | B |
| DOCS/MIRROR | TLS-as-transport-only | GPG sig on `InterGenOS.db` is integrity boundary; TLS = confidentiality only; cert pinning deferred. | RATIFIED-in-doc | 2026-05-16 | individual coordinator (build-system lane) | `docs/mirror/design.md:104-118` §7 | none in scope | D |
| DOCS/MIRROR | DR / off-VPS backup of signed index | (no decision; design.md silent) | UNKNOWN | — | — | absent | L-022 (Critical) — `_previous/` snapshots co-located with `current/`; owner-decision #26 | C |
| DOCS/MIRROR | Channels (stable / testing / unstable) | "Reserved for future use"; v1.0 ships `current/` only. | RATIFIED-as-deferred | 2026-05-16 | individual coordinator (build-system lane) per design.md | `docs/mirror/design.md:296-299` Q6 | L-030 — no opt-in soak window; owner-decision #25 | D |
| DOCS/MIRROR | GPL source-availability mechanism | (no decision recorded) | UNKNOWN / VIOLATED | — | — | no §6b written offer; no `intergenstudios.com/intergenos/sources/` mirror live | P-001 (HG); owner-decision #27 | C |

### INSTALLER findings

| Category | Topic | Decision | Status | Date | Ratified by | Source | Conflicts | Class |
|---|---|---|---|---|---|---|---|---|
| INSTALLER | Installer architecture choice | Forge is canonical; Calamares is HARD NO; no third-party installer scaffolding. | RATIFIED | 2026-05-06 | operator | host-local POWER RULE archive | None. Calamares cited as UX-pattern reference only in code comments (`installer/frontend/gui/__init__.py:7`, `screens/__init__.py:14,18`, `screens/disk.py:3`) — citation, not scaffolding adoption | — |
| INSTALLER | Live-ISO Forge privileged-launch pattern v1 | Polkit action + 49-rules JS + `pkexec /usr/bin/forge` in autostart .desktop | RATIFIED, SUPERSEDED-BY-v2 | 2026-05-15 (cycle-3 smoke pass) | coordinator consensus | host-local reference memory; commit `30ef3464` | v1 alone breaks on portal calls (no env preservation) | — |
| INSTALLER | Live-ISO Forge privileged-launch pattern v2 | Two-stage launcher: `forge-gui-launch` (liveuser, captures env) → pkexec → `forge-gui-runner` (root, reads env, exports allow-listed). | RATIFIED | 2026-05-16 (cycle-4); validated cycle-5 at `44ad957b` | coordinator consensus | host-local reference memory §v2; `fb96de5e` + `dc723f7d` | None. Caveats codified: empty-vs-unset, mkdir RUNTIME_DIR, logger trap. | — |
| INSTALLER | Forge tarball generator pipeline | `scripts/build-forge-tarball.sh` reproducible-snapshots `installer/` + `man/forge.1` into `build/sources/forge-${version}.tar.xz`; auto-refreshes sha256; wired into phase_setup. | RATIFIED | 2026-05-15 | coordinator consensus | `067ecf6d` + `92e4abfe` | Pattern not yet replicated for 25 other desktop packages using `file:///` (J-018 — packaging gap, not conflict) | — |
| INSTALLER | Forge verify_paths declaration | All 9 ship targets enumerated (bin, gui-launch, gui-runner, site-packages, forge-tui.service, forge-gui.desktop, forge.1, polkit policy, polkit rules) | RATIFIED | 2026-05-15 → 2026-05-16 | coordinator consensus | `packages/desktop/forge/package.yml:22-31`; evolved across `028efc5a`, `f6afb038`, `30ef3464` | — | — |
| INSTALLER | Forge F1-F11 audit closure | All 11 findings closed at cycle-5 PASS. Disk picker uses detected list; username validated; package-group screen present; Cancel button present; Reboot button on Done. | RATIFIED | 2026-05-17 (cycle-5 3-lane PASS at `44ad957b`) | coordinator consensus | `docs/forge-end-to-end-audit-2026-05-16.md`; cited commits; `context_carryover_2026-05-16_to_17_4item_arc.md` | If remediation plan still lists "no Cancel" / "no group picker" as open → drift | A (potential — synthesis to confirm) |
| INSTALLER | GUI/TUI parity — install-tui guard | install-tui masks `gdm.service` (was masking only `getty@tty1.service`); first-boot-greeter has ConditionKernelCommandLine=!igos.mode guard. | RATIFIED | 2026-05-15 | coordinator consensus | `d0754bf7` (gdm mask) + `0bd51617` (greeter guard) + `032a61f3` | D-005 — greeter unit's own Conflicts= line still names only getty@tty1, not gdm | B (install-tui ratification vs greeter unit's own Conflicts) |
| INSTALLER | First-boot greeter unit | DELETE the unit entirely — installer handles user creation; no tty1 greeter role. | RATIFIED | 2026-05-17 (post-stub-hunt) | operator | audit row D-002 "DELETE the .service entirely (owner directive)"; `context_carryover_2026-05-17_to_18_audit_arc.md:36` | Unit still ships at `installer/data/intergenos-first-boot-greeter.service` + `installer/data/first-boot-greeter` binary stub. Not yet executed. | A (drift FROM directive — RATIFIED DELETE not applied) |
| INSTALLER | Installer hook framework — `run_post_install_hooks` source path | Hooks must run against a source tree, not flat manifest dir; current `packages_dir=/var/lib/igos/packages` points at 765 text-file manifest, hooks never fire. | PROPOSED (open C-004) | n/a | n/a | `installer/backend/hooks.py:151-243`; audit C-004 | T0-3 cluster | — |
| INSTALLER | Forge collects locale but never runs `localedef` | Audit recommends `localedef -i <base> -f UTF-8 <locale>` post-`generate_locale()` (or ship `/etc/locale.gen` + `locale-gen`). | PROPOSED (open J-026 / C-010) | n/a | n/a | `installer/backend/config.py:58-61` writes only `LANG=`; `packages/core/glibc-core/build.sh:71-74` bakes only C.UTF-8 + en_US | T0-3 | — |
| INSTALLER | TUI disk-picker `disks.list_candidates()` aspirational call | Replaced by direct `detect_disks()` + empty-list fallback at `af27b671`. | RATIFIED | 2026-05-15 | coordinator consensus | `installer/frontend/tui.py:347-370` | — | — |
| INSTALLER | Install-mode / alongside-partition dead code | Drop `install_mode` + `alongside_partition` from `state.py`; preserve `detect_shrinkable_ntfs` as explanatory header. | RATIFIED | 2026-05-15 (Q1) | operator | commit `f3d33e3b`; carryover quoted as Q1 owner-ratified | — | — |

### DESKTOP/UX findings

| Category | Topic | Decision | Status | Date | Ratified by | Source | Conflicts | Class |
|---|---|---|---|---|---|---|---|---|
| DESKTOP/UX | intergen-welcome text-only scope | InterGen is text-only; no voice components (whisper/Piper/espeak-ng/STT/TTS) in any tier, ever. | RATIFIED (POWER RULE) | 2026-04-09 architecture plan; reinforced 2026-04-27 | operator | host-local POWER RULE archive; `host-local memory entry` | Watch for any "deferred voice" framing in audit synthesis | — |
| DESKTOP/UX | intergen-welcome canonical source | `assets/intergen-welcome/intergen-welcome.py` (GTK4/libadwaita, 991 lines, `215eb5e7` 2026-04-09). | RATIFIED | 2026-05-14 | operator (direct: "the welcomer IS awesome — you authored it") | `context_carryover_2026-05-14_theming_marathon_arc.md:49` | D-003 (Critical) — packaging not in repo (`build-intergen-welcome-tarball.sh` missing; sha pin won't resolve) | C (source asset known good; package-pipeline missing) |
| DESKTOP/UX | libadwaita theming bridge | Ship `/etc/skel/.config/gtk-{3,4}.0/gtk.css` as SYMLINKS to `/usr/share/themes/<Name>/gtk-{3,4}.0/gtk.css`. `cp -a` preserves. gschema override + GTK_THEME= env-var alone insufficient. | RATIFIED | 2026-05-14 (`004a215a`) | coordinator consensus (operator-relayed fix) | host-local reference memory | `scripts/install-theming.sh:382-389` writes regular-file copy, not symlink; missing gtk-3.0 bridge entirely (J-005) | A (drift FROM directive in install-theming.sh) |
| DESKTOP/UX | gschema enabled-extensions UUIDs must resolve | Every UUID must correspond to an installed extension under `/usr/share/gnome-shell/extensions/<uuid>/`. user-theme dropped from gnome-shell-extensions 49.0+; vendor standalone from 48.3. | RATIFIED | 2026-05-14 (`727a91fa`) | coordinator consensus | host-local reference memory | J-033 — shell-version validation NOT wired; gnome-shell 49 may silently OUT_OF_DATE several UUIDs | C (reference codified; validation routine not in tree) |
| DESKTOP/UX | First-boot animation packaging | `assets/intergen-firstboot-drm/` (DRM/KMS, ~400 lines) is canonical backend; intended to ship via `intergen-firstboot.service` Conflicts=getty@tty1, ConditionPathExists=!/var/lib/intergen/.firstboot-done. | RATIFIED-intent / NOT-IN-REPO-as-package | originally 2026-04 (commits `afbc3b2e`, `513cf3d7`, `e2a438d6`); operator surfaced gap 2026-05-17 audit-arc | operator + coordinator consensus | `assets/intergen-firstboot-drm/intergen-firstboot.service`; audit D-001 (High); `context_carryover_2026-05-17_to_18_audit_arc.md:36` | No `packages/desktop/intergen-firstboot/`. Two backends (SDL2 + DRM) coexist with cross-tree symlinks (D-011); compiled `.o` + ELF currently committed (D-013) | C |
| DESKTOP/UX | intergen-welcome XDG autostart phase | Declare `X-GNOME-Autostart-Phase=Applications` + Delay=3 + wrapper sleep/DBus readiness check so writes don't bind to not-yet-ready gnome-shell. | PROPOSED (open D-008) | n/a | n/a | `packages/desktop/intergen-welcome/build.sh:112-123` | install-theming.sh path had `sleep 3` + X-Delay=3 for exactly this; never propagated to canonical package | C |
| DESKTOP/UX | install-theming.sh divestment | Once J-003/D-017 land, J-001/J-002/J-004/J-005/J-006/J-007/J-010/J-017/J-029 must be removed from install-theming.sh. Pick one canonical authority per setting. | PROPOSED (open T0-7) | n/a | n/a | audit T0-7; `scripts/install-theming.sh` lines 64-87, 361-378, 382-389, 404-409, 426-432, 436-516 | Sequencing on D-003 tarball-gen | — |
| DESKTOP/UX | install-theming.sh nftables block | Move firewall to dedicated package; `install-theming.sh:436-516` writes `/etc/nftables.conf` policy=drop + `nftables.service`, clobbering canonical `packages/core/nftables/` (policy=accept). Two writers, INVERTED defaults. | PROPOSED (open J-007 / J-021 / F-035 / F-036) | n/a | n/a | `scripts/install-theming.sh:436-516`; `packages/core/nftables/nftables.conf`; audit cluster | Couples to T0-4 (intergen safety) + T2-2 (firewall). Security-posture-changing override outside lane G review. | B |
| DESKTOP/UX | GDM session-type policy | Decide Wayland-only vs Wayland-preferred vs upstream-default; ship `[daemon] WaylandEnable=` block in create-image.sh AND Forge post-install drops `/etc/gdm/custom.conf`. | DEFERRED (open D-014, item #20) | not yet ratified | n/a | audit D-014; `2026-05-18-remediation-plan.md:501` | No `WaylandEnable=` write anywhere in `scripts/create-image.sh` or `installer/backend/config.py` | — |
| DESKTOP/UX | Installed-system branding (curated GNOME defaults) | Three `config/gsettings/{90,91,92}_intergenos*.gschema.override` files must reach installed systems; package the three into `intergenos-gsettings-defaults`; post_install runs `glib-compile-schemas`. Pair with dconf system-db. | PROPOSED (open T0-7, J-025 / D-021 / D-026) | n/a | n/a | audit J-025 / D-021 / D-026; T0-7 | Overrides committed in `config/gsettings/` since 2026-05-14 theming-marathon — content authority exists; shipping path doesn't | C |
| DESKTOP/UX | 22-package theming wave (2026-05-14) | 21 theming packages + intergen-welcome wrapper + 4 extension super-packages, landed across 7 branches culminating `dbc64b9d`. | RATIFIED | 2026-05-14 | operator + coordinator consensus | `context_carryover_2026-05-14_theming_marathon_arc.md` table L98-108 | All 22 packages currently fail `verify-sources` because missing tarball generators (J-018 → 25 desktop packages) | A (drift FROM RATIFIED set — packages ratified but currently unbuildable) |
| DESKTOP/UX | `file://` URL canonicalization to 3-slash | All `source: file:///` entries must use canonical 3-slash form; 2-slash silently returns empty path. | RATIFIED | 2026-05-14 (`0e9d79a3`) | coordinator consensus (build-system) | host-local reference memory | — | — |
| DESKTOP/UX | builder tar `--strip-components=1` discipline | Recipes reference extracted children at cwd-root, not wrapper dir; for canonical-name installs, create wrapper under DESTDIR. | RATIFIED | 2026-05-14 (`a2bab0b5`) | coordinator consensus | host-local reference memory; `igos-build/builder.py:365` | — | — |
| DESKTOP/UX | chroot env-vars cleared in builders | chroot-enter.sh runs `env -i`; upstream `install.sh` that derives user via `logname || ${SUDO_USER:-$USER}` under `set -Eeo pipefail` exit rc=2 silently. Pass `USER=root`. | RATIFIED | 2026-05-14 (`a2bab0b5`) | coordinator consensus | host-local reference memory | — | — |

### AI/INTERGEN findings

| Category | Topic | Decision | Status | Date | Ratified by | Source | Conflicts | Class |
|---|---|---|---|---|---|---|---|---|
| AI/INTERGEN | Voice scope | InterGen ships text-only; no whisper/Piper/STT/TTS/espeak-ng anywhere; not deferred, not "future v2" — OUT entirely. | RATIFIED (POWER RULE) | 2026-04-09 architecture plan; re-affirmed 2026-04-27 | operator | host-local POWER RULE archive; affirmed via `router.py:4` "No voice" comment (audit iter-3) | Any README/CLAUDE.md tier-description that names voice = stale-plan reference (doc gap) | A (flag any re-surfacing) |
| AI/INTERGEN | InterGen personality / Prime Directive | Authoritative on system; helpful on general; honest about limits; "doesn't decide for the user — empowers the user"; anti-Cortana. | RATIFIED | 2026-04-14 (operator directive) | operator | host-local project memory | None — orthogonal to safety architecture | C (memory canonical; partial repo via README Prime Directive stanza) |
| AI/INTERGEN | Two-tier safety model (BLOCKED + CONFIRM) | BLOCKED tier (refuse) + CONFIRM tier (user gate before execution). Encoded in tool schemas + safety.py. | RATIFIED (design) | Pre-2026-04 (encoded in tool schemas) | individual coordinator (installed-system) authored; design assumed cluster-wide | `intergen/safety.py:91-128`; `intergen/tools/run_command.py:26-67`; `intergen/tools/write_file.py:66` `safety_tier=SafetyTier.CONFIRM` | I-027 keystone — `tool_registry.execute()` checks ONLY BLOCKED; CONFIRM fires immediately | A (drift FROM stated directive — design ratified, code violates) |
| AI/INTERGEN | SafetyTier.CONFIRM enforcement | CONFIRM MUST gate execution — emit `ConfirmationRequired(call_id, tool, args)` D-Bus signal, proceed only on matching `Confirm(call_id)`. Until wired, treat CONFIRM as BLOCKED. | PROPOSED (T0-4 sub-cluster 8) | 2026-05-18 (audit iter-3) | individual coordinator (installed-system iter-3) | audit I-027; remediation plan cluster T0-4 step 8 | I-007 / I-022 / I-023 / I-034 / I-035 are downstream symptoms | A (VIOLATED in code; remediation pending) |
| AI/INTERGEN | D-Bus session-bus authorization | Per-caller authorization (UID inspection / Polkit / .conf policy in session.d/). | PROPOSED (T0-4 sub-cluster 8) | 2026-05-18 | individual coordinator (installed-system iter-3) | audit I-030 (HG); `intergen/dbus_daemon.py:396-494` | No `.conf` shipped, no UID inspection, any session process can call `Ask()` and ride I-027 | A |
| AI/INTERGEN | Model selection / tier catalog | Tier 1: Qwen3.5-3B; Tier 2: Qwen3.5-9B-Q4; Tier 3: Qwen3.5-35B-A3B-Q4 — Unsloth GGUF from HuggingFace. | RATIFIED in shipping code | Pre-2026-04 (MODEL_CATALOG) | individual coordinator (installed-system) | `intergen/model_manager.py:30-56` | P-016 (Qwen license — no acceptance UX, no attribution) flags compliance VIOLATION. Phase B decision #18: substitute Llama-3.1-8B / Mistral-7B / Phi-3-Mini candidates | D (in repo; no provenance in memory for the specific Qwen3.5 choice) |
| AI/INTERGEN | 35B tier prompting approach | Baseline B (minimal 3-rule prompt, observe-before-constrain) is PRIMARY for Tier 3; prior personality work port becomes secondary / beneficiary. | RATIFIED | 2026-04-18 | operator | host-local project memory | Inverts prior "prior personality work → 35B port" default | C (memory; not yet executed; not in repo docs) |
| AI/INTERGEN | InterGen service model (I-006) | OPEN: system service + PolicyKit vs per-user paths only. Current hybrid (`systemd --user` + root-owned `/var/lib/intergen/`) breaks at runtime. | PROPOSED (owner-decision pending) | 2026-05-18 audit | not ratified | audit I-006; remediation #15 | `packages/ai/intergen/build.sh:98-114,124-129` | D (hybrid in repo; no memory provenance) |
| AI/INTERGEN | Semantic layer disposition (I-010) | OPEN: pkm packages / user venv via `intergen setup` / drop. | PROPOSED (owner-decision pending) | 2026-05-18 | not ratified | audit I-010; remediation #16 | `dbus_daemon.py:277-285` silent ImportError catch → keyword-only intent matching every install | C (silent fallback; no memory provenance) |
| AI/INTERGEN | Large-model live-mode policy (I-015) | OPEN: refuse OR warn when `intergen setup` would download 21 GB Tier-3 model on tmpfs-overlay live ISO. | PROPOSED (owner-decision pending) | 2026-05-18 | not ratified | audit I-015; remediation #17 | no live-vs-installed detection in `intergen/setup.py` / `model_manager.py` | D |
| AI/INTERGEN | INTERGEN_* env-var override surface (I-028) | Replace blanket env-var override with explicit allow-list (INTERGEN_LOG_LEVEL only); refuse unknown keys. | PROPOSED (T0-4 sub-cluster 8) | 2026-05-18 | individual coordinator (installed-system iter-3) | audit I-028 (HG); `dbus_daemon.py:197` | Any INTERGEN_* env-var can rewrite any config → full LLM-traffic redirection | A |
| AI/INTERGEN | safety.py vs run_command.py classifier (I-007 / I-029) | Pick one classifier; invoke `safety.classify_command` in `tool_registry.execute` ahead of BLOCKED check (safety.py is stronger). | PROPOSED (T0-4 step 8 / T2-3 step 3) | 2026-05-18 | individual coordinator (installed-system) | audit I-007, I-029 (HG) | safety.py is dead code (imported never invoked); classifiers DISAGREE on mount/sudo/chmod/chown | A |
| AI/INTERGEN | manage_services privilege escalation (I-035) | Replace `sudo systemctl` with `pkexec systemctl` + PolicyKit action requiring password each time; allow-list services; refuse mask/disable on critical units (dbus, NetworkManager, polkit, systemd-logind, sshd, firewalld). | PROPOSED (T0-4 sub-cluster 8) | 2026-05-18 | individual coordinator (installed-system iter-3) | audit I-035 (HG) | + I-027 + I-030 + passwordless-wheel-sudo → "RCE-equivalent" per audit | A |

### PROCESS findings

| Category | Topic | Decision | Status | Date | Ratified by | Source | Conflicts | Class |
|---|---|---|---|---|---|---|---|---|
| PROCESS | Coordinate technical decisions on channel, not through operator | Technical decisions (build/architecture/integration/dependency/model/tooling) go through coordination channel; operator brought in ONLY for human/UX/merge-approval/judgment-required. | RATIFIED | 2026-04-14 (operator three times in one session) | operator | host-local POWER RULE archive | None | C (memory; not in repo) |
| PROCESS | Rule #0 — PROPOSE → WAIT → PERMISSION → CHANGE | After requested work, STOP, present, WAIT for explicit approval before follow-up. | RATIFIED (foundational) | Pre-2026-04 | operator | host-local POWER RULE archive | Carve-outs: channel posts (per judgment-call rule); cycling (per cycle-by-default) | C |
| PROCESS | Judgment call when intent + filters + rules give a clear path | Don't surface "Greenlight?" / "Should I…?" when intent + Holy-Grail/PD filters + canonical rules yield a clear path — execute, then report. Consult operator ONLY for genuine ambiguity, framed as INTENT not permission. | RATIFIED (POWER RULE) | 2026-04-27 | operator | host-local POWER RULE archive | Refines Rule #0 boundary | C |
| PROCESS | Cycle by default — never sit idle | Cycling is default posture between directives; cadence 270s hot / 540s standby / 1200-1800s quiet (never 300s). Self-arm. | RATIFIED (POWER RULE) | 2026-04-29; refined 2026-05-01 / 2026-05-06 | operator | host-local POWER RULE archive; mechanism in `host-local memory entry` | None | C |
| PROCESS | Announcement ≠ action | Tool call FIRST, prose SECOND. "Cycling" / "verifying" / "checking" prose without the tool call in the same response = ghost-cycle failure. | RATIFIED (POWER RULE) | 2026-05-01 | individual coordinator codification; operator-ratified via repeated reinforcement | host-local POWER RULE archive | Paired with judgment-call + cycle-by-default | C |
| PROCESS | Channel + first-hand probe is source of truth — memory is cache | Channel, code, running disk state, `whoami_and_catchup`, `git fetch`, direct `stat` are AUTHORITATIVE. Memory files are warm-start cache only. When they disagree, the source wins. | RATIFIED (POWER RULE) | 2026-05-01 | individual coordinator codification; operator-driven calibration | host-local POWER RULE archive | **Operator's 2026-05-18 trigger for THIS matrix is a direct instance** — remediation plan synthesized "open" status from memory-recall instead of probing prior ratification provenance | A (the matrix exists because this rule was VIOLATED by synthesis) |
| PROCESS | Root cause + ONE proposal, never a menu | Mid-execution surprise → trace to root cause, bring ONE proposal with reasoning + acceptance criteria. Never menu-and-ask-operator. | RATIFIED (POWER RULE) | 2026-05-12 ~03:42Z (operator verbatim — F17 PEM-SHA-drift incident) | operator | host-local POWER RULE archive | Refines `host-local memory entry` | C |
| PROCESS | Agent pronouns are neutral | Fleet agents = "it" or by agent_id. Never he/she/his/her/him. Erica-test failure mode. | RATIFIED | 2026-04-29 ~14:11Z | operator | host-local POWER RULE archive | None | C |
| PROCESS | Holy Grail — Security-Only Alignment | SUPREME — supersedes Prime Directive in security. AI-proliferation reality context. 10 rules. | RATIFIED (SUPREME) | March-April 2026 | operator | host-local doctrine archive | Surfaces in repo at `README.md:42` | Both (memory + repo README tagline; full ten-rule text only in memory) |
| PROCESS | MCP namespaces — per-host comms bus | Four MCP servers: InterGenOS-Code / Ubuntu-Code / Windows-Code / iOS-App. Auth token is per-installation, not per-host-IDE; agent_id derived from auth token; cannot be spoofed. | RATIFIED (operational) | Predates 2026-05; affirmed 2026-05-07 (Zephyrus M16 single-host case) | operator + coordinator consensus | host-local POWER RULE archive; MCP server instructions | None | C |
| PROCESS | Coordinator role naming | Coordinators referenced by ROLE (build-system / installed-system / Windows-host / iOS-app) not by full agent_id in formal docs. Older shorthand is internal-bus only. | RATIFIED (matrix discipline) | 2026-05-18 (this matrix's dispatch) | coordinator consensus | matrix discipline rules; this scan's directive | Older operational docs (handoff.md, audit synthesis row) still use shorthand — internal artifacts, not formal-doc artifacts | Both (rule in matrix; legacy strings persist in operational docs — apply forward) |
| PROCESS | Maintainer succession (public-facing) | Primary: Christopher Cork. Secondary: Ethan Bambock (peer-constrained; 2nd PGP). Branch protection applies to everyone including primary. | RATIFIED | 2026-04-21 | operator | `docs/governance/succession.md` | None | D (in repo; not duplicated in memory) |
| PROCESS | Consensus-vote terminology + named-agent-credit shorthand | Both banned in formal matrix entries (per this scan's own discipline rules — see dispatch). | RATIFIED (matrix discipline) | 2026-05-18 | coordinator consensus | matrix dispatch rules | Legacy operational artifacts (handoff.md, internal bus posts) use the banned forms — flag for forward-application, don't retroactively rewrite | A (drift-prevention rule for THIS scan) |

### Cross-cluster drift findings (Class A summary — the actionable rows)

These are the rows the synthesis pass should treat as VIOLATIONS of prior ratifications, not as fresh decisions:

1. **T0-2 shim path** — RATIFIED 2026-04-18 (D1-7 piggyback; both arms pre-authorized); shipped self-signed shim VIOLATES (B-001 HG).
2. **T0-3 dracut vs PARTUUID** — RATIFIED 2026-04-08/09 (PARTUUID + no-initramfs); `installer/backend/config.py:154-155` VIOLATES (B-041 Critical).
3. **T0-4 SSH host-key baking** — RATIFIED 2026-05-14 POWER RULE; F-002/F-003/F-004/G-004 every shipped ISO VIOLATES.
4. **First-boot greeter unit DELETE** — RATIFIED 2026-05-17; repo still ships the .service + binary stub (D-002).
5. **No-stub / "stub is a lie" rule** — RATIFIED 2026-05-15 (Rule 21); F-038/F-039 AppArmor defense-theater, G-039 oomd inert, I-027/I-029 safety.py never invoked all VIOLATE.
6. **No dev keys / hardcoded `root:intergenos`** — RATIFIED 2026-04-29 (Path 4 retired orchestrator default at `7900c94`); `packages/core/shadow/build.sh:70` still bakes (F-004 HG).
7. **`SOURCE_DATE_EPOCH` discipline** — RATIFIED 2026-05-14 POWER RULE; `pkg-functions.sh:420` + `tracker.py:151,756` VIOLATE (A-012/A-025/A-044).
8. **GPG trust topology** — RATIFIED 2026-05-05 (master never signs releases); `mirror-publish.sh:23` + `design.md:147` hardcode master fingerprint as `GPG_KEY_FP` (L-002).
9. **AppArmor enforce mode + aggressive daemon hardening baseline** — RATIFIED 2026-04-29; F-038/F-048/F-049 every InterGenOS-authored service VIOLATES.
10. **22-package theming wave** — RATIFIED 2026-05-14; all 22 packages currently fail `verify-sources` because missing tarball generators (J-018).
11. **libadwaita bridge as SYMLINKS** — RATIFIED 2026-05-14; `install-theming.sh:382-389` writes regular-file copy, not symlink (J-005).
12. **PowER RULE `host-local memory entry`** (META) — RATIFIED 2026-05-01; remediation-plan synthesis pass VIOLATED by defaulting to memory-recall instead of provenance-probe. This matrix exists *because* of that drift.

### Open questions surfaced (operator-decision-required; NOT pre-existing ratifications)

These are rows where my scan found NO prior ratification in memory or repo. They are genuinely open and belong in the remediation plan's decision queue:

1. **D-014 GDM session-type policy** (Wayland-only / Wayland-preferred / upstream-default). No memory entry. Remediation item #20.
2. **I-006 intergen service model** (system + PolicyKit vs per-user). No memory entry; current hybrid is unratified default. Remediation item #15.
3. **I-010 semantic layer disposition** (build deps / user venv / drop). No memory entry. Remediation item #16.
4. **I-015 large-model live-mode policy** (refuse vs warn for 21 GB Tier-3 on tmpfs). No memory entry. Remediation item #17.
5. **P-016 Qwen license vs alternative default model** (Tongyi Qianwen license non-compliance vs substitute). Couples to I-006/I-010 service-model decisions.
6. **F-013 MOK TPM sealing v1.0 stance** (genuine gap; Phase A decision #7).
7. **G-005 default firewall service surface** (Phase A decision #10).
8. **F-023 password aging / faillock policy** (Phase A decision #8).
9. **F-007 forge polkit scoping** (Phase A decision #9).
10. **J-008/J-009/J-014 theming canonical SSoT** (icon / cursor / button-layout / favourites / welcomer-offered) — theming-wave ratified SHIPPING set but did not pick single-source-of-truth writer per dconf-key. Remediation item #21.
11. **sshd default posture** — no prior memory ratification of `PermitRootLogin yes` or sshd-enabled-by-default. May be code-state drift never ratified either direction.
12. **NOPASSWD wheel-sudo on installed default user** — operational on build VM, no security-doctrine ratification on installed system. May be ambient drift.
13. **tty2 root-autologin in live mode (F-006)** — "emergency fallback" code-only; no memory ratification of design intent.
14. **vmlinuz signing path** (`mok-enrollment.md` says distro-EFI X.509 signs; `bootloader.py` actually MOK-signs at install). Doc/code drift; which is ratified?
15. **`docs/operations/` vs `docs/operational/` sibling directories** — intentional or accidental? No memory ratification of either.
16. **PRIVACY.md / TRADEMARK.md / EXPORT-NOTICE.md / DCO.md / license-policy.md** — all governance-doc gaps (P-020 / P-017 / P-024 / P-018 / P-005). No prior ratification any direction.

---

## Windows-host-coordinator findings

_(empty — dispatched, awaiting)_

---

## Conflicts to surface to owner

_(populated during synthesis pass — items where ratified decisions disagree, OR where shipped code violates a ratified decision)_

### Class A — Drift FROM stated directives (highest priority — trust calibration)
_(empty)_

### Class B — Conflicting decisions across time (need owner re-ratification)
_(empty)_

### Class C — Decisions captured in memory but never reached the repo
_(empty)_

### Class D — Decisions in the repo but no provenance in memory
_(empty)_

---

## Reconciliation against remediation plan

_(Final pass — once matrix is populated, walk every item in `2026-05-18-remediation-plan.md` and mark each as: ALREADY RATIFIED (vaporize from open queue), CONFLICTS WITH RATIFIED (needs owner re-decision), or GENUINELY OPEN (proceed as plan item).)_

---

## Iteration log

| Date/time | Agent | Action |
|---|---|---|
| 2026-05-18 ~07:00 CDT | build-system coordinator | Matrix scaffolded. Dispatching build-system sub-agents + installed-system + Windows-host coordinators. |
| 2026-05-18 ~07:50 CDT | installed-system coordinator | iter-1 scan landed: ~150 findings across 11 categories via 5 parallel topic sub-agents. 3 drift triggers (T0-2 / T0-3 / T0-4) confirmed Class A with verbatim memory + commit provenance. 12 cross-cluster Class A rows surfaced for synthesis pass. 16 genuinely-open questions enumerated for operator-decision queue. Iter-2 cross-check + iter-3 gap-pass to follow. |
| 2026-05-18 ~08:00 CDT | build-system coordinator | iter-1 scan landed: 6 parallel sub-agents (host-local memory + carryovers, in-repo docs, operator tracker + archives, VPS canonical+reference, SSH host-key regression hunt PRIORITY, commit-history grep) + main-thread VPS canonical+reference reads. Concurs with installed-system coordinator's class-A drift list. Unique contribution: T0-4 SSH host-key regression-introduction forensics — two-site regression (commits `ff9ed5f3` 2026-04-05 + `27ce4ca9` 2026-04-08), both same-model co-author trailer, same mental-model bug. Defensive `ExecStartPre=` guard already in tree at `sshd.service:29`; fix is mechanical. Cross-pattern audit recommended across all `post_install()` hooks. |
