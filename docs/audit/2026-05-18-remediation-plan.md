# InterGenOS — Comprehensive Remediation Plan

**Date:** 2026-05-18
**Audit master:** [2026-05-18-comprehensive-state-audit.md](2026-05-18-comprehensive-state-audit.md)
**Synthesis owner:** SPOC
**Status:** v1.0 ship blocked. Hard halt on ISO builds remains in force until Tier-0 and Tier-1 clusters land.

---

## Executive summary

- **Total findings synthesized:** ~662 across all 16 lanes (SPOC A:45 / B:53 / C:70 / E:43 / M:57; IGOSC D:35 / F:58 / G:50 / I:43 / J:38; WC H:33 / K:27 / L:31 / N:19 / O:35 / P:26).
- **Severity tally:** 30+ Holy-Grail-class, 30+ Critical, 145+ High, balance Medium / Low / Cosmetic.
- **Cycle-5 verdict.** The "3-lane validated" cycle-5 ISO at `build/intergenos-1.0-dev1-smoke.unsigned-test.iso` is NOT ship-candidate. M-026 establishes the cycle-5 validator has zero pass/fail assertions; M-001/M-003/M-004 establish no install lane ever ran end-to-end; C-001/C-003/C-004/C-065/B-026/B-041 establish the installer cannot install on real hardware (parted missing, shim-signed never staged, post-install hooks no-op, archive_dir resolves to empty so packages-installed=0 yet phase reports success, no initramfs generation, `root=UUID=` cmdline with no initramfs userspace); E-001/E-002/F-001 establish microcode never loads on any boot path on any CPU vendor; F-002/F-003/F-004/G-004 establish every shipped ISO has identical SSH host keys + root-SSH-on + hardcoded `root:intergenos` password baked into shadow; H-002/L-015/O-004 establish the shipped `/etc/pkm/repos.conf` is silently ignored due to INI/JSON mismatch; L-001 + P-001 establish the signed mirror is theatre (zero packages ever published, no GPL source-availability mechanism); P-015 + P-016 establish two legal ship-blockers (ffmpeg `--enable-nonfree` non-redistributable; Qwen-model bundled with no license acceptance UX); I-027 + I-030 + I-035 establish intergen's safety/permission model is fiction (CONFIRM-tier never enforced; D-Bus session bus has zero per-caller auth; manage_services auto-`sudo`s on CONFIRM with passwordless wheel-sudo, making the LLM root-equivalent).
- **Recommended ship posture.** **Delay v1.0 by 6-8 weeks minimum.** This is up from the initial 4-6 week estimate because iter-3 surfaced an additional 11 Holy-Grail-class findings (intergen safety architecture absent; replay/anti-rollback gap on signed index; ffmpeg-nonfree + Qwen license confirmed). Two parallel tracks: (a) close all Tier-0 + Tier-1 clusters (~4-5 weeks coordinator-parallel work, conditional on owner decisions landing fast), (b) submit shim-review PR (6-12 week external dependency — start immediately or accept self-signed CA + mandatory MOK UX for v1.0). Scope reduction is NOT a viable substitute — the broken pieces are load-bearing primitives (boot, install, upgrade, microcode, SSH host keys, supply-chain trust, intergen safety, legal redistribution).
- **Top 5 Tier-0 ship-blockers** (out of 7 clusters):
  1. **Microcode triple-failure stack** (F-001 + E-001 + E-002 + F-005) — every shipped ISO runs CPUs unpatched.
  2. **Signed-boot + shim + installed-system boot chain** (B-001 + B-002 + B-005 + B-025 + B-026 + B-041 + B-042 + B-043 + A-001 + C-003 + F-024) — self-signed shim ships, install-gui/install-tui UKIs never signed, `root=UUID=` + no initramfs, MOK ordering broken, no MS sponsorship.
  3. **Installer cannot install** (C-001..C-010 + C-065 + A-003 + A-005 + A-006 + B-026 + B-041) — parted missing, post-install hooks no-op, empty-archive-dir succeeds, no initramfs, no locale-gen.
  4. **SSH-host-key + root-account + intergen-safety architectural absences** (F-002 + F-003 + F-004 + F-006 + G-004 + G-021 + C-050 + I-027 + I-028 + I-029 + I-030 + I-035 + F-035 + F-036 + F-038 + F-039) — every install ships with shared host keys + root-SSH-on + intergen's safety model is fiction.
  5. **pkm supply-chain + upgrade catastrophe** (H-002 + H-022 + H-023 + L-001 + L-015 + L-019 + L-020 + L-021 + L-022 + O-001 + O-002 + O-003 + O-004 + O-027 + P-001) — signed mirror is theatre, no anti-rollback, no schema-version refusal, tar path-traversal, repos.conf silently ignored, `pkm upgrade linux-kernel` removes-first-then-installs (unbootable on failure), no post_install hooks ever fire on upgrade, no GPL source-availability mechanism.
  6. **Legal / redistribution** (P-015 + P-016 + P-017 + P-001 + P-002 + P-003) — ffmpeg-nonfree, Qwen license, brand-mark trademark absence.
  7. **First-boot UX + GUI defaults shipping infrastructure** (D-001 + D-003 + D-004 + D-014 + J-003 + J-018 + J-019 + J-025 + J-026) — 25 packages can't build for lack of tarball generators; the curated InterGenOS desktop ships only on the live-ISO path, not on installed systems.

---

## Tier 0 — v1.0 SHIP-BLOCKERS

### CLUSTER T0-1 — Microcode triple-failure stack

**Findings:** F-001, E-001, E-002, F-005 (Holy-Grail x4)
**Lead owner:** SPOC (build-system + kernel)
**Supporting:** IGOSC (cross-check on chroot + create-image)
**Dependencies:** None — independent surgical fixes.

**Remediation summary.** Three independent layers all fail simultaneously, so any single fix in isolation produces no visible improvement. Layer 1 (F-001): `create-image.sh:292` tests `/usr/bin/iucode_tool` but binary is at `/usr/sbin/iucode_tool` — one-line path fix. Layer 2 (E-001): `CONFIG_MICROCODE=y` is set but neither `CONFIG_MICROCODE_INTEL` nor `CONFIG_MICROCODE_AMD` is asserted, so olddefconfig leaves both at `n`. Layer 3 (E-002): `build-uki.sh:77` calls ukify with a single `--initrd=$INITRAMFS` — kernel never sees ucode cpio. F-005 closes AMD: no `packages/core/amd-ucode/` exists. Add `scripts/build-microcode-initrds.sh` emitting `intel-ucode.img` + `amd-ucode.img` from `/usr/lib/firmware/{intel,amd}-ucode/`; pass each as additional `--initrd=` to ukify (ucode first, then main initramfs).

**Verification approach.** `dmesg | grep -E 'microcode: (early|sig=|revision=)'` on freshly booted live ISO produces output on both Intel and AMD test VMs. M-020 wires the smoke check. `objdump -h igos-live.efi` shows `.initrd` PE section sized for ucode+main. `grep -E 'CONFIG_MICROCODE_(INTEL|AMD)' /boot/config-*` returns `=y` for both.

**Estimated wall time.** 4-8 hours.

**Owner decisions needed.** None.

---

### CLUSTER T0-2 — Signed-boot + shim + installed-system boot chain

**Findings:** B-001 (Holy-Grail), B-002, B-005, B-025, B-026, B-041, B-042, B-043, A-001, C-003, F-024, F-037 (Holy-Grail iter-3 — live UKI cmdline ships KERN_DEBUG + journal-to-console); adjacent: B-003, B-015, B-018, B-034, L-001, L-002, L-008, L-018.
**Lead owner:** SPOC (signing + bootloader)
**Supporting:** WC (mirror state — Lane L), IGOSC (installer-side UX)
**Dependencies:**
- A-001 (orchestrator never invokes shim-signed) BEFORE any shim chain repair tested.
- A-002 (orchestrator never builds ISO) BEFORE canonical build path can verify chain.
- B-002 / B-025 (sign-release.sh glob misses install-gui/install-tui UKIs) blocks every other signing fix from reliable testing.

**Remediation summary.** Cycle-5 ISO ships InterGenOS-self-signed shim (B-001) — no MS UEFI CA chain — so real hardware with stock dbx rejects it. Even if shim worked, sign-release.sh's glob misses install-gui + install-tui UKIs (B-002) so ops doc 03's promise (B-025) is hollow. Even if signing worked, sign-release.sh doesn't enforce patched-OpenSC (B-005) — RSA-4096 PIV fails at first UKI. Installed-system boot chain is order-broken: vmlinuz MOK-signed at install time (B-042) but MOK enrolled only via MokManager first boot (B-043 has no InterGenOS UX), AND `root=UUID=` with no initramfs (B-041) means first boot can't resolve root. F-037: live UKI cmdline `igos.mode=live loglevel=7 systemd.show_status=true systemd.journald.forward_to_console=1` ships KERN_DEBUG on public ISO — info-leak.

**Required sequence:**
1. A-001 — wire `shim-signed` into `chroot-build-core-extra.sh`.
2. A-002 — wire `phase_squashfs` + `phase_iso` after `phase_manifest`.
3. C-003 — installer fails closed with clear error before partitioning when shim missing.
4. B-002 + B-025 — extend sign-release.sh glob to `igos-install-*.efi` + post-sign count assertion.
5. B-005 + B-004 + B-023 + B-049 — all sign-*.sh default to `/usr/local/lib/opensc-pkcs11.so`, port modulus-match guard, drop PIN-in-URI.
6. B-026 + B-041 + B-042 — **Dracut/mkinitcpio NOT an option** (RATIFIED-AGAINST 2026-04-08/09 + 2026-04-10 + 2026-05-05/06 Q-INIT; reaffirmed via D-005). Fix is one-line PARTUUID parity in `installer/backend/config.py:155` (root=UUID= → root=PARTUUID=) — see T0-3 sub-cluster 4. B-042 vmlinuz signing handled by D-005's per-kernel UKI signing path (no separate build-time distro CA signing needed once UKI parity lands).
7. B-001 owner-decision (below).
8. B-043 — print MOK password to persistent file + first-boot service prompts user; OR pre-stage cert into shim vendor_db (F-024).
9. B-003 + B-018 + B-034 — wipe stale `.signed`, regenerate manifest atomically with ISO, per-build `build/cycleN/{unsigned,signed,iso}/` lineage.
10. F-037 — strip `loglevel=7 systemd.show_status=true journal.forward_to_console=1` from live cmdline; route debug variant via separate `cmdline.live-debug.txt`.

**Verification approach.** Real-hardware (or OVMF + `secboot_vm_profile.py` per M-008 extension) SB-on boot: ISO → shim accepted → grub → kernel → install → reboot → MokManager → enrollment → vmlinuz loads → multi-user.target. M-006 post-sign-release.sh gate (`sbverify` walk + cert match + count assertion); M-016 fixture test for `intel-ucode.img` + ESP layout; B-016 build-iso.sh `sbverify` against `VENDOR_CERT` when `UNSIGNED_TEST=0`.

**Estimated wall time.** 2-3 weeks BLOCKED on B-001 owner-decision. Shim-review (B-015 — PR slipped past 2026-05-15 target) adds 6-12 weeks external dependency if MS-signed path chosen.

**Owner decisions needed.**
- **B-001 SHIM PATH.** (a) Fedora-piggyback shim (already packaged) ships immediately + shim-review PR filed in parallel — RECOMMENDED. (b) Wait for MS-signed shim. (c) Self-signed CA + mandatory MOK enrollment UX. June 27 2026 MS UEFI 2011 CA expiry adds further pressure.
- **B-006 measured-boot scope.** Implement `systemd-cryptenroll` LUKS+TPM2 OR drop "measured boot" claim from docs.
- ~~**B-008 / B-026 installed-system boot architecture.** UKI parity OR traditional grub-loads-vmlinuz. Initramfs generator (dracut vs mkinitcpio) follows.~~ **RESOLVED 2026-05-18 via D-005** (UKI parity, signed by user's MOK); initramfs question already-settled by 2026-04-09 ratification + D-001 narrowing (no installed-system initramfs for plain installs; tiny FDE-only initramfs bundled inside UKI for LUKS installs). No dracut, no mkinitcpio — RATIFIED-AGAINST multiple times.

---

### CLUSTER T0-3 — Installer cannot install

**Findings:** C-001, C-002, C-003, C-004, C-005, C-006, C-007, C-008, C-009, C-010, C-065, A-003, A-004 (downgraded Medium), A-005, A-006, B-026, B-041, J-026 (locale-gen never runs).
**Lead owner:** SPOC (installer + chroot package set)
**Supporting:** IGOSC (post-install hook semantics)
**Dependencies:**
- A-002 (orchestrator wires ISO) AND M-001 (real install harness) unlock verification.
- C-021 (run_chroot binary-presence pre-flight) surfaces parted-class regressions loudly.

**Remediation summary.** The cycle-5 ISO declared "installer ready" cannot install on real hardware. C-001/A-003 parted missing; C-002 partprobe missing (same package); C-003 bootloader.py raises RuntimeError on shim staging (root cause = A-001); C-004 post-install hooks NEVER fire — `packages_dir=/var/lib/igos/packages` is flat manifest dir of 765 text files, not source tree; C-005 retry cp corrupts nesting; C-006 virtual_fs double-mounts leak; C-007 `ai` group selectable in TUI but undefined in `GROUPS`; C-008 install-time signing smoke check uses wrong field names (always fails, silently masks audit-log breaks); C-009 smoke framework lands under `site-packages/installer/smoke/` not `/usr/lib/intergenos/`; C-010 + J-026 locale-conf written but `localedef` never invoked → silent fallback to C; C-065 empty `archive_dir` succeeds as zero-package install. A-005 xfsprogs missing; A-006 mdadm missing. B-026 no initramfs generator; B-041 `root=UUID=` cmdline.

**Sub-cluster ordering:**
1. **Package set:** parted (A-003), partprobe (auto), xfsprogs (A-005), mdadm (A-006), dialog (A-004 — optional), ntfs-3g (A-008 / C-014), os-prober (C-054), gptfdisk (A-007). **Dracut is NOT in this list and never will be** — RATIFIED-AGAINST 2026-04-08/09 (PARTUUID + no-installed-system-initramfs), reaffirmed 2026-04-10 ("No casper, no dracut, no mkinitcpio"), Q-INIT commits `77b0b453`/`cfcdc82e`/`a12216a5` 2026-05-05/06 explicitly rejected dracut-uefi, NARROWED-BY D-001 + D-005 (custom busybox+cryptsetup FDE-initramfs bundled inside UKI; plain installs use minimal/empty initramfs in UKI). The B-026 "no initramfs generator" finding is the WRONG framing — there is intentionally no generator; the fix lives in sub-cluster 4 below.
2. **Chroot wiring:** `chroot-health-check.sh` + `scripts/check-installer-runtime-deps.py` (M-002).
3. **Installer code fixes:** C-003 through C-010 + C-021 + C-065 + J-026 localedef wire.
4. **Boot architecture: Forge install-time PARTUUID parity** (B-026 + B-041). One-line fix: `installer/backend/config.py:155` currently emits `root=UUID=<fs-uuid>` — must emit `root=PARTUUID=<part-uuid>` to match the ratified image-time path (`scripts/create-image.sh:148-150,177`). Fstab in `config.py:22,32` likewise. NOT a dracut-vs-PARTUUID decision — that was settled 2026-04-08/09. See `docs/audit/2026-05-18-design-decisions-matrix.md:160,650` for the matrix-scan call.

**Verification approach.** M-001 real install harness — extend `secboot_vm_profile.py` to attach blank target, autotype TUI through Confirm, reboot, `pkm verify --fast` on installed root. M-002 chroot-binary-presence gate. M-005 unit-test floor for disks.py + bootloader.py + hooks.py.

**Estimated wall time.** 2-3 weeks (concurrent with T0-2).

**Owner decisions needed.** Alongside-install primitives keep + complete (C-014) OR delete as v1.x deferred. (Dracut-vs-PARTUUID is NOT an open decision and never has been within this project's lifetime — see sub-cluster 4 above.)

---

### CLUSTER T0-4 — SSH host-key + root-account + intergen-safety architectural integrity

**Findings:** F-002 (HG), F-003 (HG), F-004 (HG), F-006 (HG), G-004 (HG twin), G-021 (twin), F-007, F-008, F-009, F-010, F-013, F-015, F-016, F-035 (HG iter-3 — install-theming.sh nftables clobbers canonical + service + bypasses preset), F-036 (HG), F-038 (HG — intergen.service runs as root with zero hardening), F-039 (HG — AppArmor profiles ship but no transitions configured), B-030, C-050 (HG), C-051, I-027 (HG iter-3 — SafetyTier.CONFIRM never enforced; entire two-tier safety model is fiction), I-028 (HG — INTERGEN_* env-var blanket loop accepts any config key), I-029 (HG — safety.py BLOCKED taxonomy imported but never invoked), I-030 (HG — D-Bus session-bus methods have zero per-caller auth), I-035 (HG — manage_services auto-`sudo`s on CONFIRM with passwordless wheel-sudo = LLM root-equivalence), I-023 (High — sudo in CONFIRM_COMMANDS bypasses entire safety taxonomy), I-031..I-040 (defense-in-depth gaps).
**Lead owner:** IGOSC (Lane F + Lane I)
**Supporting:** SPOC (build-squashfs cleanup + chroot package edits).
**Dependencies:** Coordinate to avoid sequencing collisions on openssh/build.sh, mok.py, install-theming.sh.

**Remediation summary.** Two architectural-absence themes: (a) every shipped ISO ships identical SSH host keys + root-SSH-default-on + hardcoded credentials + permissive PAM; (b) intergen's safety/permission model — documented as two-tier with user confirmation gates — is implemented as no-op or wired-but-uncalled-dead-code. Net: any prompt-injected web_search result hitting intergen via D-Bus's no-auth Ask method goes straight to disk via write_file.execute (which writes BEFORE confirmation regardless — I-034), with manage_services able to mask dbus/sshd/systemd-logind via passwordless sudo. A local browser exploit becomes root-on-disk one prompt-injection away.

**Sub-cluster ordering:**

1. **Strip baked creds + keys.** F-002 / G-004: remove `ssh-keygen -A` from openssh/build.sh post_install; add `rm -f /etc/ssh/ssh_host_*_key*` + `rm -f /etc/shadow` (regen with locked root) to build-squashfs.sh cleanup. F-004: replace hardcoded `root:intergenos` with `usermod -p '*' root`. F-008: remove ssh-keygen -A from create-image.sh; rely on sshd.service ExecStartPre per-boot.
2. **sshd hardening.** F-003 / G-021: `PermitRootLogin no` (or `prohibit-password`); F-009: drop sshd.service from default-enable; F-016: curated sshd_config (MaxAuthTries 3, LoginGraceTime 30, mozilla-modern KexAlgorithms/Ciphers/MACs, ClientAliveInterval).
3. **PAM password policy.** F-010 + C-050 + C-051: pam_pwquality + pam_faillock; `/etc/security/pwquality.conf` (min 8 + non-alpha); enforce at frontend AND backend; mirror MOK password 8-256 pattern.
4. **Live mode.** F-006: tty2 root-autologin → liveuser-autologin, or gate `igos.live-root-debug=1` cmdline.
5. **Polkit scoping.** F-007: 49-intergenos-forge.rules skips install on installed targets (live-ISO-only) OR gates rule's YES on `/proc/cmdline contains igos.mode=live`.
6. **MOK key hygiene.** F-013: TPM-sealed-MOK toggle; F-015: bump MOK_KEY_BITS to 3072 or 4096.
7. **install-theming.sh nftables hard-purge.** F-035 / F-036 / J-007 / J-021: move the firewall block to a dedicated package (per Lane J synthesis); kill the divergent service-enable in install-theming.sh; coordinate with T2-2 firewall posture.
8. **intergen safety architectural fix (HG-class).** I-027: tool registry returns PENDING for CONFIRM-tier and emits D-Bus signal `ConfirmationRequired(call_id, tool, args)`; only proceeds on matching `Confirm(call_id)`; until then treat CONFIRM as BLOCKED. I-028: replace env-var blanket loop with explicit allow-list of safe overrides (INTERGEN_LOG_LEVEL only); refuse unknown keys with warning. I-029: invoke `safety.classify_command` in tool_registry.execute ahead of BLOCKED check (preferred — safety.py is the stronger classifier), OR delete safety.py and document run_command.py as authoritative. I-030: ship `/usr/share/dbus-1/session.d/com.intergenos.InterGen.conf` restricting send_destination + check sender UID; Ask refuses messages producing CONFIRM/BLOCKED tool dispatch until I-027 closes. I-035: replace `sudo systemctl` with `pkexec systemctl` + PolicyKit action requiring password each time; restrict allowed services to allow-list; refuse mask/disable on critical units (dbus, NetworkManager, polkit, systemd-logind, sshd, firewalld). I-023: move `sudo` to BLOCKED_COMMANDS.
9. **intergen hardening surface.** F-038: intergen.service runs as root with zero `NoNewPrivileges=`, `ProtectSystem=`, `ProtectKernelTunables=`, `RestrictRealtime=`, `RestrictSUIDSGID=` — add full sandboxing. F-039: AppArmor profiles ship but no orchestrator transitions them — wire `apparmor_parser -r` on profile-bearing package install (couples to T2-3 / O-032). I-031 / I-032 / I-033 / I-034 / I-036 / I-037: log scrubbing + file-locking + MCP trust-tier gate + write_file confirm-before-write + read_file BLOCKED_READ_PREFIXES + llama_manager rlimits/cgroup. I-039: SentinelGuard injection-pattern scan on inbound Ask messages.

**Verification approach.** Smoke check additions to `installer/smoke/checks/signing.sh`: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` fingerprint MUST differ from build-host's; `grep -E '^root:[*!]' /etc/shadow` (locked); `grep -E '^PermitRootLogin (no|prohibit-password)' /etc/ssh/sshd_config`; `systemctl is-enabled sshd` returns `disabled` unless opt-in; `grep pam_pwquality /etc/pam.d/system-auth`. New intergen tests: drive Ask through D-Bus from a non-owner UID + assert refusal; emit CONFIRM-tier tool call + assert PENDING + assert no FS write until Confirm; emit attacker-crafted `INTERGEN_LLM_ENDPOINT=...` + assert rejection. M-043a evasion tests for safety.classify_command. Wire all into `--release` smoke (M-010).

**Estimated wall time.** 1-1.5 weeks for SSH/PAM/MOK; 1.5-2 weeks for intergen safety re-architecture. Largely independent of T0-1/T0-2/T0-3.

**Owner decisions needed.**
- Password aging (F-023): chage defaults OR explicit "no aging by design."
- v1.0 stance on MOK TPM sealing (F-013) — couples to B-006.
- F-007 polkit scoping path (cmdline-gate vs namespace-rename liveuser).
- I-006 intergen service model: system service + PolicyKit OR per-user paths only.

---

### CLUSTER T0-5 — pkm supply-chain + upgrade catastrophe

**Findings:** H-001, H-002 (HG), H-022 (HG iter-3 — tar path-traversal protection missing), L-001 (HG — signed mirror is theatre), L-015 (HG iter-2 — INI/JSON repos.conf mismatch silently nullifies shipped config), L-019 (HG iter-3 — no anti-rollback/replay-attack defense), L-020 (HG — no schema-version refusal), O-001 (Critical — `pkm reinstall` referenced but doesn't exist), O-002 (Critical — kernel upgrade leaves system unbootable on partial failure), O-003 (Critical — `pkm upgrade` never executes post_install hooks), O-004 (Critical — shipped repos.conf format mismatch), O-027 (Critical iter-3 — `pkm upgrade` no-args silent mass-modify with no confirmation/dry-run), L-021 (Critical — TOCTOU verify-vs-extract), L-022 (Critical — no off-VPS DR backup for signed index), H-023 (Critical — no process-level lock; concurrent pkm invocations race on FS deploy), H-024 (Critical — helper installer inherits full env), K-017 (Critical — `pkm sync` documented but is not a valid CLI subcommand), L-002 (HG — mirror-publish.sh signs with offline MASTER fingerprint instead of subkey), L-003 (Critical — pkm/repo.py default URL points at non-existent host), L-004 (Critical — shipped repos.conf points at source-tarball mirror not binary), L-006 (High), L-007 (High), L-008 (High — pkm GPG keyring never created), H-004 (Critical — installer never records dependencies → reverse-dep safety dead), H-005 / H-006 / H-007 / H-008 / H-009 / H-018 (smoke + DB hygiene Highs), H-019 (High — `cmd_upgrade` can brick critical packages), O-005..O-035 (full upgrade-system gap list), L-005 (High — two contradictory publish workflows), L-009 (Medium — promote-shape conflict), L-023..L-031 (mirror-side verifier / transparency log / pinning / DoS), P-001 (HG — no GPL source-availability mechanism).
**Lead owner:** WC (Lane H + Lane L + Lane O)
**Supporting:** SPOC (build-system + signing-ceremony staging the keyring), IGOSC (cross-check on integrity.py smoke).
**Dependencies:**
- H-001 + L-008 keyring (precondition for any verify/sync); L-015 + O-004 (precondition for any pkm CLI testing); H-002 (repo URL fix); L-005 (canonical publish workflow ratified).

**Remediation summary.** pkm is the canonical install/update/upgrade path — every primitive in it is broken or unsafe:

- **Mirror is fiction.** L-001: signed mirror story permeates user-facing docs but zero packages have ever been signed/published; `first-publish-runbook.md:4` literally states "The procedure described here has never been exercised end-to-end." P-001: no GPL source-availability mechanism (GPLv2 §3 / GPLv3 §6 textbook violation on first redistribution).
- **Shipped config silently broken.** H-002 + L-015 + O-004: `/etc/pkm/repos.conf` is INI, parser expects JSON → silent fallthrough to DEFAULT_REPOS (L-003 — wrong host). L-004: shipped URL points at source-tarball mirror, not binary. Every shipped install fetches from wrong URL with no signal.
- **Signing model wrong.** L-002: mirror-publish.sh hardcoded with offline MASTER fingerprint instead of signing subkey. Operationally collapses offline-root posture. L-007: per-archive `.sig` designed for + served + documented but never generated. L-018: docs point at wrong PIV slot. L-006: apache snippet never deployed.
- **Trust chain weakness.** L-008: GPG keyring `/etc/pkm/trusted.gpg` never created — every sync fails closed. L-019: no anti-rollback/replay defense — attacker pins clients to old vulnerable snapshot. L-020: schema-version silently accepted at any value, no sig-algorithm-agility. L-021: TOCTOU between SHA256 verify and tar extract — cache file swappable. H-022: tar extraction uses subprocess `tar` with no path-traversal protection — `../` member escapes staging.
- **Upgrade catastrophic.** O-001: `pkm reinstall` referenced + doesn't exist. O-002: `pkm upgrade linux-kernel` `remover.remove(force=True)` BEFORE installing new — partial failure = unbootable system. O-003: post_install hooks NEVER fire on upgrade (depmod, ldconfig, gtk-update-icon-cache, glib-compile-schemas, ca-certificates trust — every upgrade leaves system degraded). O-027: bare `pkm upgrade` mass-modifies all upgradable packages with no confirmation/dry-run/-yes flag → compounds O-002/O-007 catastrophically. H-019: cmd_upgrade can brick critical packages on partial failure. O-007: docs claim "atomic supersedes transactions" — actually remove-then-install with no atomicity, no snapshot, no rollback.
- **Installer-time DB integrity.** H-004: dependencies never recorded (`add_depends` exists but never called) → `pkm remove glibc` succeeds without `--force` and destroys system. H-007: helper installer never registers files or deps. H-008: tier/description/license/build_date never populated. H-005: smoke invokes nonexistent `pkm query`. H-006 + H-018: `pkm verify` and `pkm verify --all` both return exit 0 in failure cases — smoke can't detect.
- **DR / transparency / off-VPS.** L-022: no off-VPS backup for signed index + `_previous/` snapshots co-located. L-023: no mirror-side verifier. L-024: no transparency log / cosigner. L-025: trust anchor mutable post-install with no integrity check.
- **User UX.** K-017: every user doc tells users `sudo pkm sync` as day-one command; subcommand doesn't exist (`update` is the real name). User hits "unknown command" on day one.

**Sub-cluster ordering:**

1. **Keyring + repo URL + config format** (preconditions): L-008 keyring (couples with T0-2 signing-ceremony output); H-002 + L-015 + O-004 repos.conf format align; L-003 + L-004 URL ratification; A-031 chroot pre-stage.
2. **Mirror infrastructure** (precondition for any meaningful publish): L-005 ratify one workflow (publish-repo.sh vs mirror-publish.sh); L-006 deploy apache snippet; L-009 align promote-shape; L-002 fix signing fingerprint; L-018 fix PIV slot doc; L-007 decide per-archive-sig (v1.0 vs v1.1+).
3. **Trust-chain hardening:** L-019 anti-rollback (persist `last_seen_generated` per-repo under PKM_STATE_DIR; 7-day freshness ceiling; 24h Valid-Until envelope; reject older — landed at b274080a); L-020 schema-version refusal + `min_pkm_version` + `signature_format` + `arch` validation; L-021 re-hash + flock; H-022 switch to `tarfile.extractall(filter="data")`; L-024 transparency log; L-025 pin fingerprint in release-keys.json + reject non-pinned-key validation.
4. **CLI + DB integrity:** K-017 add `sync` subparser as alias of `update`; O-001 implement `pkm reinstall`; H-004 wire `add_depends` from manifest; H-007 helpers emit manifest pkm consumes; H-008 populate tier/description/license; H-005 fix smoke; H-006 + H-018 fix verify exit codes; H-009 fix `import sys`; H-011 + H-021 add `root` param to PackageDB; H-023 fcntl flock; H-024 strip env to allowlist; H-013 fix `--archive` multi-pkg silent-drop.
5. **Upgrade safety:** O-027 require explicit `--yes` or interactive y/N for bare `pkm upgrade`; add `--dry-run`. O-002 install-new-first-then-remove-old; refuse `linux-kernel` upgrade without `--allow-kernel-replace`; kernel-aware post-install (depmod + grub-mkconfig + initramfs regen + UKI re-sign). O-003 ship `post_install` hook scripts inside .igos.tar.gz; installer extracts + executes after deploy + DB commit. O-005 resolve new deps. O-006 + O-021 user-modified config files preserved + DB baseline preserved. O-007 install-new-then-remove-old ordering + future btrfs-snapshot integration. O-008 bootloader+initramfs integration on kernel install. O-009 log `upgrade` operation type. O-010 version-aware comparison. O-013 cache GC. O-014 delete dead `INDEX_MAX_AGE` constant (replaced by L-019 state-tracked freshness; landed at 2ee16c57). O-015 autoremove. O-016 transaction log. O-024 retry-with-backoff + Range resume. O-025 free-disk-space preflight. O-026 `pkm hold`. O-029 needrestart-style service-restart prompts. O-031 mirror failover.
6. **DR + transparency + GPL source:** L-022 nightly off-VPS rsync to cold storage + secondary mirror in release-keys.json + index-loss recovery runbook + git-committed signed-index backup. L-023 daily VPS-side verifier cron. L-024 transparency log (signed indexes to public append-only log + planned cosign integration). P-001 GPLv3 §6b written offer in LICENSE/COPYING.offer + `/usr/share/doc/intergenos/SOURCES` + bring `intergenstudios.com/intergenos/sources/` mirror live with §6d access promise — BLOCK v1.0 ship until done.
7. **User doc sweep** for `pkm sync` -> add alias OR rewrite to `pkm update` (K-017 + O-012 + O-019 + O-020).

**Verification approach.** End-to-end pkm flow: fresh install → `pkm sync` (or `pkm update`) succeeds; `pkm search <pkg>` returns results; `pkm install` + `pkm upgrade` + `pkm verify --all` succeed with correct exit codes; M-027 / M-029 / M-030 / M-017 tests author the integration coverage; rollback test (M-034) asserts real unmount on real failure; replay-attack test injects old signed index + asserts refusal; tar path-traversal test injects `../` member + asserts refusal. Smoke checks: `gpg --verify InterGenOS.db.sig InterGenOS.db` succeeds on installed system; `ssh-keygen -lf` host-key uniqueness; new `pkm verify-trust-anchor` subcommand asserts trusted.gpg matches release-keys.json. M-010 `--release` mode turns SKIPs into FAILs.

**Estimated wall time.** 3-4 weeks. Largest cluster by code surface.

**Owner decisions needed.**
- ~~**L-005 canonical publish workflow** (publish-repo.sh vs mirror-publish.sh); hostname (repo.intergenos.org vs intergenstudios.com/mirror/).~~ **RESOLVED 2026-05-18 via mirror-sweep:** Hostname was ratified DAY-0 (2026-05-11, operator-direct) + `intergenos.org` registrar-locked 5 years + infra live since 2026-05-11. The "open" framing was scan-memory-first failure. Sweep: `mirror-publish.sh` deleted, `docs/mirror/design.md` rewritten against ratified state, `packages/core/pkm/build.sh:42-44` URL fixed, `docs/mirror/apache-userdata-snippet.conf` deleted (moot — `repo.intergenos.org` has its own cPanel subdomain vhost). `scripts/publish-repo.sh` confirmed canonical. Remaining work is E1.B.5/E1.B.6/E1.B.7 (build-pipeline emission + signed index + first publish) — implementation backlog, not owner-decision.
- **L-007 per-archive `.sig`** v1.0 omit OR v1.1+ defer with explicit triggers OR implement now.
- **O-011 release-channel model** (single-repo vs stable/testing/edge); per-package channel pinning.
- **L-022 DR scope** (nightly cold-storage backup; secondary mirror?).
- **P-001 GPL §6 path** (written offer in LICENSE + SOURCES.md OR network access via mirror).
- **K-017 `pkm sync` resolution** (alias vs rewrite all docs).

---

### CLUSTER T0-6 — Legal / redistribution

**Findings:** P-001 (HG — covered in T0-5 above for the source-mirror dependency), P-015 (HG iter-3 — ffmpeg `--enable-nonfree --enable-libfdk-aac` produces non-redistributable binary while declaring LGPL), P-016 (HG iter-3 — intergen auto-fetches Qwen LLM weights with no license inventory, no user acceptance, no attribution surface, no use-restriction disclosure), P-017 (Critical iter-3 — InterGenOS brand assets under GPL-3 default, no trademark statement, no carve-out — competitor could fork+rebrand+malware), P-002 (Critical — LFS/BLFS attribution declares CC-BY-NC-SA), P-003 (Critical — FDK-AAC patent restrictions ignored), P-004 (High — no THIRD-PARTY-NOTICES / per-package LICENSE), P-005 (High — AGPL ghostscript/mupdf compliance not audited), P-006 (High — helper packages mislabel proprietary downstream as GPL-3), P-007 (Medium — zero SPDX headers), P-008..P-013, P-014 (Cybernetic icon theme attribution), P-018..P-026 (iter-3 DCO sign-off, fonts/sounds/wallpapers attribution, PRIVACY.md, vendored Rust licenses, mariadb FMT license drift, cert/key fixtures, BIS export notice, toolchain licenses, gnome-extensions per-zip LICENSE extraction).
**Lead owner:** WC (Lane P)
**Supporting:** SPOC (ffmpeg build flags + add SPDX headers script + license-text bundling), IGOSC (Qwen UX integration + intergen-welcome + intergen MODELS.md).

**Remediation summary.** Multiple legal ship-blockers stack:

- **P-015 ffmpeg-nonfree.** `packages/desktop/ffmpeg/build.sh:13-33` invokes `--enable-nonfree --enable-libfdk-aac`. FFmpeg docs explicit: resulting libavcodec.so/ffmpeg binary is non-redistributable. `package.yml:5` declares `license: LGPL-2.1-or-later` which is demonstrably impossible. Action: drop `--enable-nonfree` and `--enable-libfdk-aac` for default ffmpeg build (standard Fedora/Debian choice); ship separate `ffmpeg-nonfree` opt-in helper if needed. Update license to truthful compound. v1.0 ship-blocker.
- **P-016 Qwen license.** `intergen/model_manager.py` MODEL_CATALOG auto-fetches three Qwen3.5 GGUF quantizations under Tongyi Qianwen License (source-available with restrictions: 100M MAU clause, commercial restrictions, requires "Powered by Qwen" attribution, use-restrictions for military/surveillance/CSAM). System has zero license-text bundle, zero click-through acceptance, zero attribution surface, zero CREDITS entry, zero `models:` inventory. Action: bundle Qwen license text into `/usr/share/licenses/intergen/MODELS/`; firstboot/firstrun acceptance gate; "Powered by Qwen" attribution in intergen UI; per-model license verification at fetch time; `intergen/MODELS.md` license inventory; MODELS section in CREDITS; `payload_license:` field convention. v1.0 ship-blocker. Owner-decision: default model substitute (Llama-3.1-8B-Instruct / Mistral-7B-Instruct-v0.3 / Phi-3-Mini are license-permissive options).
- **P-017 brand trademark.** `assets/intergen-mark/` ships InterGenOS logo files under root GPL-3.0 default — anyone can redistribute/modify/use commercially without restriction. No TRADEMARK.md. Practical risk: competitor forks + rebrands as "InterGenOS Plus" with official mark + ships malware. Action: TRADEMARK.md at repo root; carve `assets/intergen-mark/` and intergen-shell-theme name field out of GPL-3 with CC-BY-ND-style policy; consider USPTO wordmark registration before public ISO ship.
- **P-002 LFS/BLFS CC-BY-NC-SA.** Triggers on commercial distribution; affects derivative documentation. Action: update version drift + add Methodology-vs-Verbatim disclaimer.
- **P-003 FDK-AAC.** Patent + redistribution restrictions; standard distros omit from main. Action: move to opt-in/non-free OR drop from default ISO entirely OR document patent posture in `LEGAL/PATENT-NOTICE.md` + require explicit user opt-in via helper pattern.
- **P-001 GPL source-availability.** Covered in T0-5 cluster T0-5 step 6.
- **P-004 + P-010 + P-014 + P-019 + P-021 + P-022 + P-026 per-package LICENSE bundling.** Build-time extraction of upstream LICENSE/COPYING into `/usr/share/licenses/<pkg>/LICENSE`; emit top-level `THIRD-PARTY-NOTICES.md`; cargo-c crate license inventory via cargo-license; mariadb fmt MIT addition; gnome-extensions per-zip LICENSE extraction.
- **P-005 AGPL audit.** Audit InterGenOS service surface (intergen, pkm, forge) for ghostscript/mupdf network-exposure. Document AGPL-3 compliance in `docs/governance/license-policy.md` (does not exist; create).
- **P-006 helper proprietary license labelling.** Split into `license:` (helper script GPL-3) + `payload_license:` (proprietary). Add click-through EULA per ToS.
- **P-007 SPDX headers.** Add `# SPDX-License-Identifier: GPL-3.0-or-later` + `# Copyright (C) 2015-2016, 2026 InterGenJLU` to all 228 InterGenOS-authored source files via `scripts/add-spdx-headers.py`.
- **P-018 DCO sign-off.** Adopt DCO 1.1; `DCO.md` at repo root; CONTRIBUTING.md requires `Signed-off-by:` trailer; extend public-content-audit workflow.
- **P-020 PRIVACY.md.** Required by GDPR Art. 13/14 and CCPA §1798.130 even for purely-local processing. Add root + `/usr/share/doc/intergenos/PRIVACY` + firstboot greeter link.
- **P-024 BIS export notice.** Add `docs/legal/EXPORT-NOTICE.md`: 5D002 ENC TSU classification; embargoed jurisdictions.

**Verification approach.** `grep -E 'nonfree|gpl-incompat' build/intergenos-*.iso.manifest` returns nothing; ffmpeg binary `ldd` clean; model_manager refuses Qwen auto-download without `--accept-license`; pre-squashfs audit greps all package.yml license fields against allowlist; `find /usr/share/licenses -type f -name LICENSE` returns one per shipped package; SPDX-headers presence asserted by CI.

**Estimated wall time.** 1.5 weeks (ffmpeg 1-2 days; Qwen UX 3-5 days; brand+SPDX+per-pkg LICENSE bundling 3-5 days; remaining items background).

**Owner decisions needed.**
- **Default model** for `intergen setup --auto` if not Qwen.
- **ffmpeg-nonfree-helper** ships in mirror tier or stays out of repo entirely.
- **P-003 fdk-aac path.**
- **USPTO trademark registration** timing.

---

### CLUSTER T0-7 — First-boot UX + GUI defaults shipping infrastructure

**Findings:** D-001 (High — first-boot animation built but never shipped), D-002 (Medium — greeter binary stub; owner-ratified DELETE), D-003 (Critical — intergen-welcome tarball doesn't exist), D-004 (Critical — 4 intergenos-extensions tarballs), D-005, D-006, D-007, D-008, D-009, D-010, D-011, D-012, D-014; D-017 (Critical iter-2 — 6th tarball-gap package); D-021 (HG iter-3 — dconf system-db structurally dead-lettered on installed systems); D-026 (HG iter-3); J-001 / J-002 / J-004 / J-005 / J-006 / J-010 / J-017 / J-029; J-003 (High — 7 InterGenOS-authored desktop packages cannot build); J-018 (High iter-2 — file:/// pattern broken across 25 desktop packages); J-019 (High iter-2 — intergenos-default-settings build.sh references payload files that don't exist anywhere in tree); J-021 (High iter-2 — install-theming.sh nftables clobbers canonical; couples to T0-4 / T2-2); J-007 / J-011 / J-012 / J-013 / J-015 / J-016 / J-020 / J-023; J-025 (High iter-3 — gsettings overrides shipped only on live-ISO path, installed systems boot vanilla GNOME 49); J-026 (High iter-3 — Forge collects locale but never runs localedef; couples to T0-3); J-027 (High iter-3 — broken-dock-pin to firefox.desktop which isn't installed); J-030..J-038 (xdg-user-dirs, mimeapps.list, GDM greeter dconf, extension shell-version validation, portals.conf, IME, sound theme, a11y, HiDPI).
**Lead owner:** IGOSC (Lane D + Lane J)
**Supporting:** SPOC (tarball-generation scripts wiring into `phase_setup`; intergenos-gsettings-defaults packaging).
**Dependencies:**
- D-003 / D-004 / J-003 / J-018 / J-019 BLOCKS J-001/J-002/J-004/J-005/J-006/J-010 remediation (install-theming.sh divestment) until canonical packages are buildable.
- D-021 / D-026 / J-025 fix BLOCKS the installer-side branded UX (installed systems currently boot vanilla GNOME).

**Remediation summary.** Two architectural-absence themes:

(a) **25 packages can't build for lack of tarball generators.** J-018 extends D-003/D-004/J-003: file:/// pattern broken across 25 `packages/desktop/*/package.yml`, not just the 7 InterGenOS-authored ones. Only `forge-1.0.0.tar.xz` exists in `build/sources/`. The verify-sources phase will HARD FAIL all of them. install-theming.sh has been masking the gap for 6 of them on the qcow2 path with divergent dconf/file writes; once J-003 is resolved AND install-theming.sh divests, the system actually ships them properly. J-019: intergenos-default-settings build.sh references payload files (`01-intergenos-defaults`, `1775735161994164.conf`) that don't exist anywhere committed — author the payloads in-tree FIRST.

(b) **Curated InterGenOS desktop ships only on live-ISO; installed systems boot vanilla GNOME.** D-021 + D-026 + J-025 (Holy-Grail / High): `config/gsettings/{90,91,92}_intergenos*.gschema.override` are copied into the image by `create-image.sh:251-256` only. Forge's `generate_all()` never copies them; no package ships them. Installed-system users inherit none of the curated dark color-scheme, theme/icon/cursor pins, terminal palette, login-banner, night-light, tap-to-click, button-layout, or favourite-apps pins. J-026: Forge collects a locale but never runs `localedef` — chosen locale doesn't exist on target. J-027: dock pins `firefox.desktop` but firefox is tier:extra. J-030..J-034: xdg-user-dirs / mimeapps.list / GDM greeter dconf / extension shell-version validation / portals.conf all absent.

**Sub-cluster ordering:**

1. **D-002 delete** (owner-ratified).
2. **Author missing payloads.** J-019: intergenos-default-settings `01-intergenos-defaults` + `1775735161994164.conf` in-tree. Generate theme preview thumbnails for J-011.
3. **Tarball generators (D-003/D-004/J-003/J-018).** Author `scripts/build-intergen-welcome-tarball.sh`, `scripts/build-intergenos-theme-tarball.sh`, `scripts/build-intergenos-default-settings-tarball.sh`, `scripts/build-intergenos-extensions-<cat>-tarball.sh` x4 (OR single parametric `scripts/build-igos-pkg-tarball.sh` taking `pkg + version`). Wire into `phase_setup` like `build-forge-tarball.sh`. Repeat the sweep for all 25 `packages/desktop/*/package.yml` with `file:///` sources.
4. **D-001 intergen-firstboot packaging.** Create `packages/desktop/intergen-firstboot/`; ship binary + session wrapper. Resolve B-010 tty1 contention with first-boot-greeter (D-002 deletion mostly closes this).
5. **install-theming.sh divestment.** Once D-003/J-003 land, delete divergent write blocks (J-001/J-002/J-004/J-005/J-006/J-010/J-017/J-029). Move firewall block to dedicated package (J-007/J-021) — couples to T0-4 / T2-2.
6. **J-025 + D-021 + D-026 installed-system branding.** Package the three gschema override files into `intergenos-gsettings-defaults` (or extend intergenos-default-settings); post_install runs `glib-compile-schemas`. Same for dconf system db. J-026 wire `localedef`. J-027 fix dock-pin to default-installed app. J-030 xdg-user-dirs in users.create_user. J-031 ship mimeapps.list. J-032 GDM greeter dconf. J-033 extension shell-version validation at build time.
7. **Brand assets + polkit-gnome.** D-010 / J-016 / J-012 packages.
8. **J-013 idle-lock policy explicit decision** in gschema overrides.

**Verification approach.** Boot installed system through GDM greeter → user login → welcome wizard renders with previews and brand mark → completion writes done-marker → second login no re-show. Add `installer/smoke/checks/desktop.sh` per M-039: `check_desktop_gdm_enabled`, `check_desktop_welcome_present`, `check_desktop_theme_loaded`, `check_desktop_locale_active`, `check_desktop_gsettings_overrides_applied`. Pre-squashfs audit: assert extension UUIDs in `enabled-extensions` resolve to installed dirs.

**Estimated wall time.** 1.5-2 weeks; sequencing-bound by D-003/J-003 landing first.

**Owner decisions needed.**
- icon-theme + cursor-theme + button-layout single-source-of-truth (J-008/J-009/J-028/J-029).
- Trim 11 GTK themes to the 8 welcomer offers (J-014/J-024).
- GDM session-type policy (D-014): Wayland-only vs Wayland-preferred vs upstream-default.
- IME / sound theme / accessibility defaults (J-035 / J-036 / J-037 / J-038).

---

## Tier 1 — CRITICAL CORRECTNESS

### CLUSTER T1-1 — Orchestrator end-to-end completeness

**Findings:** A-002 (Critical — also in T0-2), A-022, A-023, A-028, A-029, A-032..A-045, M-046, M-048, M-049, K-002 / K-003 / K-004 / K-006 / K-013 / K-015 / K-016 (doc-drift).
**Lead owner:** SPOC (build system)
**Dependencies:** A-002 must land before any other build-pipeline fix can be tested via canonical path.

**Remediation summary.** The orchestrator never executes the path it documents. `phase_image` only produces QCOW2; `build-squashfs.sh` + `build-iso.sh` are run separately via 80+ ad-hoc `spoc-*.sh`/`run-*.sh` kickoff scripts. QCOW2 in `build/` is 10 days old. `.build-phase` says `core`. K-015 + K-016: phase 17 `manifest` doc claims Rule 18 reconciliation but `phase_manifest` emits sha256 archive manifest; `phase_publish` prints typo `pk sync`. Fix: (1) wire `phase_squashfs` + `phase_iso` after `phase_manifest`; (2) move/delete 80+ kickoff scripts to `build/_archive/<date>/`; (3) M-046 phase-script alignment test; (4) M-048 kickoff-coverage test; (5) M-049 shebang + `bash -n` test; (6) doc sweep aligning all 12 K-* phase-references with reality.

**Verification approach.** `bash scripts/build-intergenos.sh --user christopher --iso` from clean chroot produces a bootable signed ISO at canonical path with no manual steps. M-046 + M-048 + M-049 gates enforce in CI. M-026 cycle-validator gains real assertions.

**Estimated wall time.** 1-2 weeks.

**Owner decisions needed.** A-028 QCOW2 retirement (recommended); disposition of 80+ archived kickoffs.

---

### CLUSTER T1-2 — Service hygiene + systemd unit correctness + sysctl/journald/logind drop-ins

**Findings:** G-001..G-033 + G-034..G-050 (iter-3); C-019, C-022, C-023; B-010, B-035, D-005, D-006, D-007, B-040. Includes critical newcomers from iter-3: G-036 (booby-trap once G-005 lands: nftables ExecStop flush ruleset becomes momentary firewall-bypass on restart); G-037 (no NetworkManager.conf — MAC randomization off, IPv6 privacy off, DNS plugin ambiguous); G-038 (no logind.conf — RemoveIPC/KillUserProcesses/HandleLidSwitch/IdleAction all upstream-defaults); G-039 (systemd-oomd enabled but inert without policy + accounting); G-040 + G-041 (no system.conf + no .slice files); G-042 (update-pki.timer killed by G-033 + 99-default-disable catch-all); G-043 (LLMNR still broadcasts hostname); G-035 (UsePAM appended every reinstall — drift accumulator); G-046 (fcron `&bootrun` burst on first post-install boot).
**Lead owner:** IGOSC (Lane G)
**Supporting:** SPOC (chroot-config + presets); coordinates with T0-4 (sshd hardening) and T2-2 (firewall).
**Dependencies:** G-036 MUST land WITH G-005 (T2-2) — otherwise fixing G-005 to policy=drop introduces a booby-trap on every nftables restart.

**Remediation summary.** Systematic walk of each unit per the per-finding fix; consolidate sysctl/journald/logind/NM/system.conf/oomd drops in a single `intergenos-baseline-config` (or per-tier) package; align preset policy (per-package `systemctl enable` calls retire in favor of `80-intergenos-enable.preset` extension per G-013/G-033/G-042). Add tmpfiles.d for the 7+1 missing-RW-path packages (G-001/G-002/G-011). Fix Type/Restart/EnvironmentFile/After-network-online gaps (G-008/G-009/G-016/G-022/G-031). Wire SystemdService= on intergen D-Bus activation (G-015). Add `intergen.slice` + `databases.slice` (G-041) so oomd can fire correctly when memory pressure hits. Fix G-035 idempotency (grep-guard before append). Stagger fcron Delay= (G-046). For G-031 (NM-wait-online disabled), document the trap; G-022 remediation must NOT naively `After=network-online.target`.

**Verification approach.** `systemctl --failed` returns empty on freshly booted install; `journalctl -b -p err` returns no service-start errors; M-015 per-system extension to smoke-services.list; `nft list ruleset` shows policy=drop on restart (post-G-036+G-005 fix); `loginctl show-session | grep KillUser` shows yes; `oomd-ctl status` shows policy active.

**Estimated wall time.** 1.5-2 weeks.

**Owner decisions needed.** None standalone (correctness fixes); BT_BREDR / mDNS / firewall service-surface owner decisions coalesce in T2-2.

---

### CLUSTER T1-3 — pkm correctness + dependency tracking (subset of T0-5 broken out for parallelism)

This cluster is the operational baseline for pkm working at all even without the supply-chain trust fixes from T0-5. Items here can land independently and unlock pkm self-testing:

**Findings:** H-001 keyring (T0-5 sequence step 1), H-004 / H-007 / H-008 (DB integrity), H-005 / H-006 / H-009 / H-011 / H-013 / H-018 / H-021 (CLI + smoke + Python integrity), A-037, M-017 / M-027 / M-028 / M-029 / M-030 (test coverage).

See T0-5 for cross-references. Splitting here so the test-authoring work (M-027..M-030) can fire concurrently with the supply-chain trust hardening in T0-5.

**Estimated wall time.** Bundled into T0-5 phasing.

---

### CLUSTER T1-4 — Test infrastructure rebuild (M-class consolidation)

**Findings:** M-001..M-056 (57 findings) + M-043a; collapses to 7 actionable initiatives:

1. **End-to-end install harness** (M-001/M-003/M-004/M-008/M-034/M-037/M-038): extend `secboot_vm_profile.py` to drive real installs against blank target disks; MOK enrollment via SPICE keystrokes; per-phase cancel.
2. **Chroot-binary-presence enforcement** (M-002): `check-installer-runtime-deps.py` + extension to `chroot-health-check.sh`. The gate that would have caught T0-3's parted/iucode_tool class.
3. **Signed-ISO artifact verification** (M-006/M-016/M-020/M-036): `sbverify` walk + microcode runtime check + ISO loop-mount assertions, wired post-`sign-release.sh` and as `--release` smoke mode.
4. **pytest/coverage canonicalization** (M-012/M-013/M-014/M-025/M-042/M-047/M-049/M-055/M-056): `pyproject.toml` with `[tool.pytest.ini_options] testpaths = [...]`; `pythonpath` covers in-tree packages; coverage floor 60% v1.0; `tests/README.md`; bats/shellspec for bash tests; CI workflow runs suite.
5. **Stubs gate as standing CI** (M-009): `scripts/check-aspirational-stubs.py`. First full-tree audit, then pre-push gate 9, then build-squashfs Step 4.6. (K-023: per docs/build-development-rulebook.md:285 Rule 21 already references this script as if it exists — reflexive Rule 21 violation that closing this initiative resolves.)
6. **Smoke-test hardening** (M-010/M-011/M-015/M-019/M-035/M-039/M-040/M-041): `--release` mode turns SKIPs into FAILs; per-check fixture suites; smoke-services.list extension; githook fixtures; desktop.sh checks; check-public-content fixtures expanded; M-026 cycle-5 validator gains real assertions.
7. **Per-module unit-test floor** (M-005/M-017/M-018/M-022/M-023/M-024/M-027/M-028/M-029/M-030/M-031/M-032/M-033/M-043/M-043a/M-044/M-045/M-050/M-051/M-052/M-053/M-054): prioritize `safety.py` (HG); `disks.py`/`bootloader.py`/`hooks.py` (T0-3); `pre-squashfs-audit.py` (HG gate); `remover.py`; `repo.py`. Property-based via `hypothesis` for validators.

**Lead owner:** SPOC (Lane M)
**Supporting:** Every coordinator authors tests for code they own.

**Estimated wall time.** 4-6 weeks parallelized; initiatives 1-3 are blockers for un-pausing ISO builds.

---

## Tier 2 — HIGH SECURITY / HARDENING

### CLUSTER T2-1 — Kernel hardening + sysctl/cmdline policy

**Findings:** E-006..E-014, E-019..E-021, E-024..E-043 (KSPP-recommended class); F-011, F-012, F-020, F-030..F-058 (iter-3 IGOSC kernel-hardening sweep including F-040..F-049 service hardening + F-050..F-058 cmdline/sysctl deltas).
**Lead owner:** SPOC (kernel — Lane E)
**Supporting:** IGOSC (Lane F + sysctl shipping coordination with G).

**Remediation summary.** Single canonical `intergenos-baseline-config` package shipping:

- **Kernel config overrides** to `99-intergenos-overrides.config`: RANDSTRUCT_FULL=y (E-006); INIT_ON_FREE_DEFAULT_ON=y (E-007); ZERO_CALL_USED_REGS / DEBUG_LIST / DEBUG_SG / BUG_ON_DATA_CORRUPTION / GCC_PLUGIN_LATENT_ENTROPY / GCC_PLUGIN_STRUCTLEAK_BYREF_ALL (E-008); GCC_PLUGIN_STACKLEAK=y (E-028); IA32_EMULATION=n + COMPAT_BRK=n (E-037); KEXEC_SIG_FORCE=y (E-010); HIBERNATION=n (E-011); LOCK_DOWN_KERNEL_FORCE_INTEGRITY=y (E-024); FW_LOADER_USER_HELPER=n (E-025); MODULE_UNLOAD=n (E-026); BT_DEBUGFS=n (E-039); BLK_DEV_LOOP/OVERLAY_FS/SQUASHFS/ISO9660_FS=y (E-019); AES_NI_INTEL=y (E-041); explicit RANDOM_TRUST_CPU=n + RANDOM_TRUST_BOOTLOADER=n (E-043).
- **Sysctl drop-in `/usr/lib/sysctl.d/99-intergenos-hardening.conf`**: `kernel.unprivileged_userns_clone=0`, `vm.unprivileged_userfaultfd=0`, `net.core.bpf_jit_harden=2`, `kernel.sysrq=0`, `kernel.perf_event_paranoid=3`, `kernel.kptr_restrict=2`, `kernel.dmesg_restrict=1`, `kernel.kexec_load_disabled=1`, `kernel.unprivileged_bpf_disabled=1`, `fs.protected_{symlinks,hardlinks,fifos,regular}=2`, `kernel.io_uring_disabled=2` (after E-030 owner decision).
- **UKI cmdlines** (F-011 + F-020 + F-037): append `lockdown=integrity slab_nomerge init_on_alloc=1 init_on_free=1 pti=on mitigations=auto,nosmt module.sig_enforce=1` to all three cmdline files. Resolve E-017 (verbose live cmdline) and F-037 (KERN_DEBUG + journal-to-console on live ISO) at the same time.
- **PAM hardening** (F-010 + F-019): pwquality + faillock + curated limits.conf — wired in T0-4 SSH hardening cluster.
- **journald + resolved + skel** (F-021 + F-022 + F-029 + G-010 + G-014 + G-043): persistent journal, DNSSEC + DoT + LLMNR=no, umask 027, /etc/skel/.ssh/ 0700.
- **logind / system.conf / oomd / slices** (G-038 + G-039 + G-040 + G-041): coordinate with T1-2.
- **Per-build verifier** (E-023 + E-029): `scripts/verify-kernel-config.py` asserts every `CONFIG_*=y/=m` from overrides survives olddefconfig.
- **Build-key cleanup** (E-012): `rm -f certs/signing_key.pem certs/signing_key.x509` in linux-kernel post-install.

**Verification approach.** Boot installed system → `sysctl -a | grep '^kernel\.kptr_restrict' = 2`; `cat /sys/kernel/security/lockdown` shows `[integrity]`; `objdump -p igos-live.efi | grep SizeOfImage`; M-020 microcode check; new `tests/kernel/test_override_survival.py` per E-023.

**Estimated wall time.** 1-2 weeks.

**Owner decisions needed.** E-030 io_uring; E-040 BT_BREDR; E-018 SMT default; E-032 LIVEPATCH=y but no infra.

---

### CLUSTER T2-2 — Firewall + network posture (Holy-Grail-adjacent)

**Findings:** G-005 (HG — nftables policy=accept on every chain), G-036 (booby-trap pairs with G-005), G-022 (After=network.target instead of online), G-031 (NM-wait-online disabled — naive G-022 fix hangs boot), G-026, F-018 (algif_aead incomplete mitigation for CVE-2026-31431), F-029 (DNSSEC/DoT off), F-035 / F-036 (install-theming.sh clobbers — covered in T0-4).
**Lead owner:** IGOSC (Lane G)
**Supporting:** SPOC.
**Dependencies:** G-005 and G-036 MUST land together (G-036 booby-trap activates as G-005 takes effect).

**Remediation summary.** Flip input + forward to `policy drop`; explicit accept for loopback + established/related + opt-in service surface; rewrite preset comment. Ship `/etc/nftables-flush.conf` baseline-deny that ExecStop replays (closes G-036). Compile out algif_aead surface (`CONFIG_CRYPTO_USER_API_AEAD=n`) instead of blacklist modprobe (F-018). systemd-resolved DNSSEC=allow-downgrade + DNSOverTLS=opportunistic (F-029) + LLMNR=no (G-043). G-022 server services Wants/After=network-online.target ONLY if NM-wait-online re-enabled (G-031 trap) — accept boot-with-no-link delay OR document operator burden.

**Verification approach.** `nft list ruleset` shows policy drop on input + forward; `systemctl restart nftables` does not flush ruleset (deny-baseline replay); smoke check `check_network_default_deny`; `cat /sys/module/algif_aead/initstate` returns absent; resolved status shows DNSSEC active.

**Estimated wall time.** 3-5 days.

**Owner decisions needed.** Default-allowed service surface; mDNS for printer discovery posture.

---

### CLUSTER T2-3 — intergen AI assistant correctness + safety (non-HG-architectural items)

**Findings:** I-004 (Critical — dbus_daemon main() never enters loop), I-005 (HG — model SHA256 TOFU), I-006, I-008, I-009, I-010, I-011, I-012, I-013, I-014, I-015, I-016, I-017..I-026, I-031..I-040 (logging / locking / MCP gate / read_file / llama_manager / SentinelGuard / analyze_file / cmd_run pattern evasion / cli ephemeral / Memory.db_path / write_file confirm-order); I-007 (safety classifier consolidation); I-001 / I-002 / I-003 / I-017..I-019 orphan + verify_paths; M-043 / M-043a per-module tests.

(Note: I-027 / I-028 / I-029 / I-030 / I-035 are addressed in T0-4 as the keystone architectural absences.)

**Lead owner:** IGOSC (Lane I)
**Supporting:** SPOC (packaging + I-003 verify_paths sidecar).

**Remediation summary.** With T0-4's architectural fixes landing (CONFIRM gate, env-var allowlist, safety.classify_command wired, D-Bus auth, manage_services PolicyKit), the remaining intergen items collapse to:

1. **I-004 main loop fix** (precondition for everything else to be reachable via D-Bus).
2. **I-005 pin model SHA256s** (couples to P-016 license-gate UX).
3. **I-007 safety classifier consolidation** (already addressed via I-029 in T0-4).
4. **I-006 service-model decision** (owner-decision below).
5. **I-010 semantic layer decision** (owner-decision below).
6. **I-012 disable global auto-enable**; opt-in via welcomer.
7. **I-008 surface intergen in welcomer** (couples to T0-7).
8. **I-016 + I-026 INTERGEN_MODEL_PATH integrity check + tier RAM ceiling validation.**
9. **M-043 module-by-module test authoring** (safety.py first; T0-4 evasion tests already address M-043a).
10. **I-001 / I-002 / I-014 orphan deletes**, **I-003 + I-017 verify_paths extension**, **I-011 / I-013 / I-018 / I-019 / I-020 / I-021 / I-024 / I-025 cleanup**.
11. **I-031 log scrubbing**, **I-032 flock + per-UID port**, **I-033 MCP trust-tier gate**, **I-034 write_file CONFIRM-gate-aware**, **I-036 read_file BLOCKED_READ_PREFIXES**, **I-037 llama_manager rlimits**, **I-039 SentinelGuard Ask scan**, **I-040 analyze_file LLMRouter routing**.

**Verification approach.** `systemctl --user is-active intergen.service` returns active after intergen setup; `intergen ask "test"` returns response via D-Bus, not CLI fallthrough; `sha256sum ~/.local/share/intergen/models/llm/*.gguf` matches pinned manifest hash; M-043 + M-043a evasion tests in `intergen/tests/test_safety.py`.

**Estimated wall time.** 1.5-2 weeks (concurrent with T0-4).

**Owner decisions needed.**
- **I-006 service model.** System service + PolicyKit OR per-user paths only.
- **I-010 semantic layer.** Build deps as pkm packages, install via setup venv with consent, OR drop semantic layer.
- **I-015 large-model live-mode policy.** Refuse OR warn.

---

### CLUSTER T2-4 — Defense-in-depth gaps + secondary security hardening

**Findings:** F-014, F-017, F-019, F-022, F-023, F-025, F-026, F-027, F-028 + F-030..F-058 (iter-3 IGOSC extension); B-006 (couples to T0-2), B-007, B-019, B-020, B-031, B-032, B-033, B-044, B-045, B-046, B-047, B-048, B-049 (covered T0-2), B-050, B-051, B-052, B-053; A-024, A-031.
**Lead owner:** IGOSC (Lane F) + SPOC (Lane B bootloader).
**Supporting:** WC (Lane K docs alignment for B-014 / B-024 / B-047 drift).

**Remediation summary.** Catch-all bucket. Highlights:

- **F-014** atd auto-enable removal; **F-017** GRUB_DISABLE_OS_PROBER=true default; **F-019** /etc/security/limits.d/00-intergenos.conf; **F-022** /etc/skel umask 027 + .ssh/ pre-create; **F-023** chage policy decision; **F-025** delete `tester` user from squashfs; **F-026** post-first-boot MOK enrollment verification + desktop notification; **F-027** sudo secure_path; **F-028** sbat.csv kernel/stub/shim entries; **F-030..F-058** kernel/service hardening Highs surfaced in Lane F iter-3.
- **B-006** measured-boot scope; **B-007** objcopy --dump-section footgun; **B-020** squashfs SHA file outside UKI envelope (threat-model doc); **B-031** ESP case-mismatch; **B-032** EFI variable cleanup on uninstall; **B-033** live initramfs has no LUKS/NVMe rescue (scope decision); **B-044** generate_enrollment_password() dead code; **B-045** MOK PHASE non-idempotent on resume; **B-046** GRUB superuser password (threat-model decision); **B-047** trust-chain doc drift; **B-048** vmlinuz signing pre-check; **B-050** unsealed MOK key on disk; **B-051** shim cert-rotation; **B-052** GRUB_DISABLE_OS_PROBER triplicate authority; **B-053** OVMF VARS template mismatch.
- **A-024** verify_paths mode/owner for setuid; **A-031** chroot keyring (couples T0-5).

**Estimated wall time.** 1.5-2 weeks background.

**Owner decisions needed.** B-046 GRUB password; B-033 rescue-medium scope; B-006 measured-boot scope.

---

## Tier 3 — STRUCTURAL DEBT

### CLUSTER T3-1 — Package set completeness + verify_paths coverage

**Findings:** A-009 (chrony), A-016, A-017, A-018, A-019, A-020, A-027 (115+79=194 packages missing/empty homepage), A-021, E-015 (NVIDIA), E-016 (ZFS); B-038 (kernel.intergenos SBAT); N-003 (no swap), N-004 (no /home), N-011..N-019 (fstab drift, min-disk-size, USB exclusion, no rollback, blkid stale, mkfs.ext4 defaults, fstrim.timer, encryption-at-rest, _split_partition_path), K-018 / K-019 / K-020 / K-025 / K-026 / K-027 (pkm(1) man page missing, intergenos-keyring package missing, 5 promised databases missing, README structure description omits installer subdirs, getting-started TUI-only framing wrong, GUI screen list incomplete).
**Lead owner:** SPOC (Lane A) + WC (Lane P license-implication for NVIDIA/ZFS) + Lane N partition gaps.

**Remediation summary.** Standard distro coverage + partition correctness. Highlights: N-011 single `_render_fstab` library; N-012 16 GiB min disk; N-013 USB exclusion; N-014 partition-rollback (`zap_disk`); N-015 fresh blkid probe; N-016 SSD-aware mkfs.ext4; N-017 fstrim.timer enabled. K-020: 5 promised databases (mongodb / redis / ferretdb / cassandra / couchdb) either ship or downgrade docs to "v1.x coming."

**Estimated wall time.** 1.5-2 weeks.

**Owner decisions needed.** NVIDIA / ZFS / 5 databases ship-tier; nmap tier; swap default (swapfile vs zram).

---

### CLUSTER T3-2 — Documentation drift (post-fix sweep)

**Findings:** A-023, B-014, B-024, B-047, C-048, C-049, C-061, C-062, K-001..K-027 (27 Lane K findings — see Lane K section); doc-implication items from every other lane (P-008 AUTHORS, P-011 README License section).
**Lead owner:** WC (Lane K).
**Supporting:** Every coordinator for docs they own.

**Remediation summary.** Post-fix sweep across `docs/operations/`, `docs/signing-procedure.md`, `docs/mok-enrollment.md`, README, user docs to reflect post-remediation reality. Critical: B-014 + B-024 + B-047 are signing/boot-chain doc drift; K-001 / K-002 / K-003 / K-005 / K-013 are phase/script/version drift; K-011 CLAUDE.md missing; K-012 stale master-ref stamps; K-022 public-hosting-plan disagrees with L-003 about whether DNS exists.

**Estimated wall time.** 3-5 days post-T0/T1.

---

### CLUSTER T3-3 — Build-system reproducibility + hygiene

**Findings:** A-012, A-025, A-044 (3 non-reproducible tar invocations), A-013-revisit, A-014-revisit, A-022, A-030, A-032..A-045, B-022 (ceremony.py untested).

**Remediation summary.** Identical fix shape for A-012/A-025/A-044: `--sort=name --mtime=@${SOURCE_DATE_EPOCH:-0} --owner=0 --group=0 --numeric-owner --pax-option=delete=atime,delete=ctime`. Orchestrator hygiene + ceremony test framework (B-022).

**Estimated wall time.** 1 week.

---

## Tier 4 — POLISH

Single section — Low + Cosmetic only, batched at end. Distributed across coordinators; background opportunistic. Includes A-039 (smoke-test ISO rotation), A-041, A-043, B-019 / B-024 / B-036 / B-038 / B-039 / B-040 / B-051 / B-052 (low items not in T2-4), C-026..C-049 low items, D-011 / D-013 / D-014, G-022..G-028 / G-047..G-050, I-001 / I-002 / I-011 / I-013 / I-014 / I-024 / I-025 / I-041 / I-042 / I-043, J-014 / J-015 / J-022 / J-024, K-012 / K-014 / K-027, L-013 / L-032, N-019, O-014 / O-018 / O-034 / O-035, P-007 / P-008 / P-009 / P-011 / P-012 (Cosmetic downshifted) / P-013 / P-026, M-040 / M-041 / M-053..M-056.

---

## Cross-cutting themes

### Test infrastructure rebuild (T1-4 expansion)
Initiatives 1-3 are blockers for un-pausing builds. Initiatives 4-7 are continuous improvement.

### Stubs gate as standing CI
M-009 → `scripts/check-aspirational-stubs.py` graduates from periodic-audit → pre-push gate 9 → build-squashfs Step 4.6. Tonight's audit (~662 findings, many of them Rule 21 stubs) is the cost of absence. K-023 ("Rule 21 itself references a script that doesn't exist — reflexive Rule 21 violation") closes when this lands.

### Fleet workspace MCP namespace
Per CLAUDE.md: SPOC uses `_-_Ubuntu_-_Code__*` only; never `_-_InterGenOS_-_Code__*`. `mcp-host-server-validate.sh` mechanically enforces.

### Operational runbook accuracy (post-fix sweep)
Pair every T0/T1 fix with corresponding doc update; do not declare a cluster closed until doc reflects reality. WC Lane K coordinates the sweep.

### Compliance posture documentation gap
P-007 SPDX headers + P-008 AUTHORS + P-011 README License expansion + P-018 DCO + P-020 PRIVACY.md + P-024 BIS export notice + P-025 toolchain license inventory + governance/license-policy.md (P-005). Without these, the project lacks the standard legal-posture surface peer distros ship and audit teams expect.

---

## Owner decision queue

Decisions blocking remediation start (or substantively affecting scope). Numbered for owner reference:

1. ~~**B-001 SHIM path.** Fedora-piggyback shim (immediate) vs MS-signed shim via shim-review (6-12 wk dep) vs self-signed CA + mandatory user enrollment.~~ **RESOLVED 2026-05-18 via D-002 (`docs/owner-directives.md`):** Ratified 2026-04-18 D1-7 — ship Fedora-piggyback shim AND pursue own MS-signed shim via rhboot/shim-review PR in parallel. Both arms pre-authorized day-0. Cycle-5 wiring drift is implementation backlog, not a fresh decision.
2. ~~**B-006 measured-boot scope.** Implement `systemd-cryptenroll` LUKS+TPM2 OR drop "measured boot" from docs.~~ **RESOLVED 2026-05-18 via D-001 (`docs/owner-directives.md`):** TPM-sealed unlock + FIDO2 unlock = v1.0 EXPERIMENTAL features, flagged in installer UI (Ubuntu 24.04 precedent). Implementation is `systemd-cryptenroll` LUKS+TPM2/FIDO2 backed by the v1.0 LUKS baseline.
3. ~~**B-008 / B-026 installed-system boot architecture.** UKI parity OR traditional grub-loads-vmlinuz; initramfs generator selection.~~ **RESOLVED 2026-05-18 via D-005 (`docs/owner-directives.md`):** UKI parity for installed-system (Option A). Per-kernel UKI on ESP signed by user's local MOK. InterGenOS PIV stays at HQ. Initramfs question already resolved by 2026-04-09 ratification (no installed-system initramfs for plain installs) + D-001 (tiny FDE-only initramfs for LUKS installs). PARTUUID drift in `installer/backend/config.py:154-155` is implementation backlog.
4. **A-002 / A-028 QCOW2 retirement** (recommended) vs dual-output transitional.
5. **A-004 dialog** add vs standardize whiptail.
6. **C-014 alongside-install** keep + complete (ntfs-3g+ntfsresize) vs delete as v1.x deferred.
7. ~~**F-013 / B-050 MOK TPM sealing** v1.0 ship-decision — couples to B-006.~~ **RESOLVED 2026-05-18 via D-001:** TPM-sealed unlock is v1.0 EXPERIMENTAL per the directive; sealing the MOK key to TPM is in scope as part of the same EXPERIMENTAL feature surface.
8. **F-023 password aging policy** chage defaults vs document "no aging by design."
9. **F-007 forge polkit rule scoping** cmdline-gate vs liveuser-namespace-rename.
10. **G-005 default firewall service surface** loopback-only? Opt-in sshd? mDNS for printer discovery?
11. **E-018 SMT default** (`mitigations=auto,nosmt` vs `auto`).
12. **E-030 io_uring** (`=y` + sysctl restrict vs `=n`).
13. **E-040 BT_BREDR** (classic BT vs LE-only).
14. **E-032 LIVEPATCH=y but no infra** (leave forward-compat vs turn off until infra ships).
15. **I-006 intergen service model** (system service + PolicyKit OR per-user paths only).
16. **I-010 intergen semantic layer** (build deps as pkm packages, setup-time venv, OR drop).
17. **I-015 large-model live-mode policy** (refuse OR warn).
18. **T0-5 / P-016 Qwen default model substitute** (Llama-3.1-8B / Mistral-7B / Phi-3-Mini).
19. **T0-6 / P-015 ffmpeg-nonfree-helper** mirror tier or out entirely.
20. ~~**D-014 GDM session-type policy** (Wayland-only / Wayland-preferred / upstream-default).~~ **VAPORIZED 2026-05-18 (operator chat greenlight):** Wayland-only is ratified `docs/VISION.md:212`. Implementation default: ship explicit `WaylandEnable=true` in `/etc/gdm/custom.conf` (packages/desktop/gdm). Not encoded as D-NNN; available for batch-encoding at end-of-walk.
21. ~~**J-008 / J-009 / J-014 theming canonical single-source-of-truth.**~~ **RESOLVED 2026-05-18 via D-006 (`docs/owner-directives.md`):** `intergenos-default-settings` gschema-override package is the SSoT. Retire `scripts/install-theming.sh` + `/etc/dconf/db/system.d/` overrides. Closes J-001/J-005/J-018/D-021/D-026/J-027 cluster. J-021 firewall logic (currently buried in install-theming.sh) needs a separate owner — flagged as out-of-scope follow-on.
22. ~~**B-015 shim-review PR timing** (close gated items NOW or accept further slip).~~ **RESOLVED 2026-05-18 via D-003:** Target 2026-05-22 stands; couples to D-002.
23. ~~**L-005 publish workflow** (publish-repo.sh vs mirror-publish.sh); hostname (repo.intergenos.org vs intergenstudios.com/mirror/).~~ **RESOLVED 2026-05-18 via mirror-sweep:** Hostname ratified day-0 (operator-direct 2026-05-11); `intergenos.org` registrar-locked 5 years; infra live. `mirror-publish.sh` deleted; `docs/mirror/design.md` rewritten; `packages/core/pkm/build.sh:42-44` URL fixed; `apache-userdata-snippet.conf` deleted. `scripts/publish-repo.sh` confirmed canonical. Remaining work: E1.B.5/E1.B.6/E1.B.7 (implementation backlog).
24. ~~**L-007 per-archive `.sig`** v1.0 omit / v1.1+ defer / implement now.~~ **RESOLVED 2026-05-18 via D-004:** Signed-index-only for v1.0; per-archive sigs deferred to v1.1+. The 2026-05-12 closure (commit `d6b3946a`) stands. Artifact-sweep across 4 doc/script surfaces is implementation backlog.
25. **O-011 release-channel model** (single-repo vs stable/testing/edge); per-package channel pinning.
26. **L-022 DR scope** (nightly off-VPS backup + secondary mirror).
27. **P-001 GPL §6 path** (written offer in LICENSE vs network access via mirror).
28. **K-017 `pkm sync` resolution** (add alias vs rewrite docs).
29. **P-002 LFS/BLFS attribution** posture (methodology vs verbatim text disclaimer).
30. **P-003 fdk-aac** keep in default vs opt-in/non-free vs drop.
31. **P-017 USPTO wordmark registration timing.**
32. ~~**N-018 encryption-at-rest** explicit v1.0 non-goal vs v1.x roadmap LUKS milestone.~~ **RESOLVED 2026-05-18 via D-001:** LUKS-at-install is v1.0 baseline (opt-in passphrase-only LUKS2). Forge gets an encryption checkbox at the partition stage; plain installs unchanged. FDE-only initramfs (busybox + cryptsetup) is the narrow exception to the no-installed-system-initramfs ratification.
33. **N-003 swap default** (swapfile vs zram).

---

## Recommended dispatch order

**Phase A — week 1** (immediate kickoff after owner decisions on items 1, 5, 18-19, 23-24, 28 land):
- **SPOC:** T0-1 microcode triple-fix; T1-1 orchestrator phase_squashfs+phase_iso wiring (A-002); T0-2 ordering steps 1-2 (shim-signed package wiring + orchestrator ISO phase).
- **IGOSC:** T0-4 SSH host-key + root-account integrity (independent, no blocker); start T0-2 step 8 MOK enrollment UX in parallel.
- **WC:** T0-5 keyring + repo URL + config format (steps 1-2); T0-6 ffmpeg-nonfree audit + Qwen license-UX design (steps 1-2); T0-6 SPDX-headers + per-package LICENSE bundling background.

**Phase B — weeks 2-3** (T0-2 + T0-3 + T0-5 in parallel):
- **SPOC:** T0-3 package-set authoring (parted/xfsprogs/mdadm/os-prober/ntfs-3g/gptfdisk — NO dracut, RATIFIED-AGAINST); T0-3 sub-cluster 4 PARTUUID parity one-liner; T0-2 steps 3-7 (signing-script hardening + installed-system boot fix); T1-1 orchestrator hygiene.
- **IGOSC:** T0-3 installer code fixes (C-003..C-010 + C-065 + C-021); T1-2 service hygiene walk; T0-7 tarball generators (D-003 / D-004 / J-003 / J-018 / J-019); T0-4 intergen safety re-architecture (I-027 / I-028 / I-029 / I-030 / I-035).
- **WC:** T0-5 trust-chain hardening (anti-rollback / schema-version / tar path-traversal); T0-5 mirror infrastructure ratification + deployment (L-005 / L-006); T0-5 CLI + DB integrity (H-004 / H-007 / H-008 / K-017); T0-6 P-017 trademark + P-002 / P-003.

**Phase C — weeks 3-4** (verification + hardening):
- **SPOC:** T1-4 initiatives 1-3 (install harness + binary-presence gate + signed-ISO verification); T2-1 kernel hardening + sysctl drop-ins; T1-1 M-046/M-048/M-049 tests.
- **IGOSC:** T0-7 first-boot UX packages + install-theming.sh divestment + J-025/J-026/J-027 installed-system branding; T2-2 firewall + DNSSEC + G-036 booby-trap pair; T2-3 intergen correctness; T1-2 service hygiene + sysctl/journald/logind/oomd/slice consolidation.
- **WC:** T0-5 upgrade safety (O-002 / O-003 / O-027 / O-005..O-035); T0-5 DR + transparency + P-001 GPL source; T3-2 documentation drift sweep; T3-1 package-set completeness; T0-6 PRIVACY.md + BIS notice + DCO + remaining P-* items.

**Phase D — weeks 4-5** (re-baseline cycle-6 ISO):
- All coordinators: re-run orchestrator end-to-end (no ad-hoc kickoffs); cycle-6 ISO produced via canonical path; M-001 harness drives real install through `pkm verify --fast` clean; M-006 signed-ISO verification post-sign-release.sh gate.
- Decision point: ship-candidate? If yes, file shim-review PR (if not yet filed) and proceed to ceremony.

**Phase E — weeks 5-7** (release-prep):
- Sign-release ceremony against cycle-6 ISO; `--release` smoke gates; M-006 + M-036 signed-ISO verification; release-key fingerprint published; SBAT entries cross-checked; intergenstudios.com mirror first-publish per first-publish-runbook.md; PRIVACY.md + EXPORT-NOTICE.md + THIRD-PARTY-NOTICES.md + TRADEMARK.md live.

**Phase F — weeks 7-8** (buffer for owner-decision lag + integration surprises). Allocate 1-2 weeks slack — synthesis underestimates risk of late-surface findings during real install harness work.

**Parallelism opportunities.** T0-1 / T0-4 / T0-5 / T0-6 are fully independent of each other AND of T0-2/T0-3 — fire all five in week 1 if owner decisions land. T2-* clusters can start mid-Phase-B opportunistically.

---

## Verification gates

Gates wired post-remediation to prevent regression:

1. **Pre-push gate 9 — Rule 21 stubs scan** (M-009).
2. **Pre-squashfs Step 4.5 — verify_paths audit** (existing; extend per A-024 for setuid mode/owner).
3. **Pre-squashfs Step 4.6 — installer-binary-presence audit** (M-002).
4. **Pre-squashfs Step 4.7 — Rule 21 stubs gate** (M-009 second tier).
5. **Post-`sign-release.sh` gate — signed-ISO artifact verification** (M-006 + M-036): `sbverify` walk; manifest match; count assertion.
6. **CI workflow — Python test suite** (M-013).
7. **CI workflow — shell test suite** (M-013 sibling).
8. **CI workflow — kickoff-rsync coverage** (M-048).
9. **CI workflow — orchestrator phase completeness** (M-046).
10. **Cycle-validator real assertions** (M-026).
11. **Smoke-test `--release` mode** (M-010).
12. **Boot smoke microcode positive check** (M-020).
13. **Coverage floor** (M-012): 60% v1.0 baseline; 80% v2.0.
14. **Kernel-override survival check** (E-023 / E-029).
15. **pkm sync anti-rollback assertion** (L-019): `last_seen_generated` monotonic increase enforced.
16. **pkm trust-anchor fingerprint pin verification** (L-025): release-keys.json drift gate.
17. **GPL source-availability assertion** (P-001): pre-publish gate verifies `intergenstudios.com/intergenos/sources/` accessible OR LICENSE contains §6b written offer.
18. **License-text bundling completeness** (P-004): per-package `/usr/share/licenses/<pkg>/LICENSE` count == installed-package count.
19. **DCO sign-off** (P-018): pre-push gate 10.
20. **Holy-Grail-class regression test** (synthesis-level): integration test asserts intergen Ask with CONFIRM-tier tool returns PENDING + no FS side-effect (closes I-027 regression class); separate test asserts `INTERGEN_LLM_ENDPOINT=...` rejected (closes I-028).

These collectively close the regression classes that produced this audit's findings.

---

*End of remediation plan.*
