# InterGenOS — Comprehensive Remediation Plan

**Date:** 2026-05-18
**Audit master:** [2026-05-18-comprehensive-state-audit.md](2026-05-18-comprehensive-state-audit.md)
**Synthesis owner:** SPOC
**Status:** v1.0 ship blocked. Hard halt on ISO builds remains in force until Tier-0 and Tier-1 clusters land.

---

## Executive summary

- **Total findings synthesized:** ~395 across 16 lanes (A:45 / B:53 / C:70 / D:14 / E:43 / F:29 / G:28 / H:1 / I:16 / J:16 / M:57 / WC-stubs:23). Lanes K/L/N/O/P show as "in progress" in the tracker header; counts above are the SPOC + IGOSC + WC populated rows.
- **Severity tally** (top tiers): 16 Holy-Grail-class, 26 Critical, 84 High; rest Medium / Low / Cosmetic.
- **Cycle-5 verdict:** The "3-lane validated" cycle-5 ISO at `build/intergenos-1.0-dev1-smoke.unsigned-test.iso` is NOT ship-candidate. M-026 establishes the cycle-5 validator has zero pass/fail assertions; M-001/M-003/M-004 establish no install lane ever ran end-to-end; C-001 / C-003 / C-004 / C-065 / B-026 / B-041 establish that the installer cannot install on real hardware (parted missing, shim-signed never staged, post-install hooks no-op, archive_dir resolves to empty so packages-installed=0 yet phase reports success, no initramfs generation, `root=UUID=` cmdline with no initramfs userspace). E-001 / E-002 / F-001 establish microcode never loads on any boot path on any CPU vendor.
- **Recommended ship posture:** **Delay v1.0 by 4-6 weeks minimum.** Two parallel tracks: (a) close all Tier-0 + Tier-1 clusters (~3-4 weeks of coordinator-parallel work), (b) submit shim-review PR (6-12 week external dependency — start NOW or accept the self-signed-CA-enroll posture for v1.0 with explicit user UX). Scope reduction is NOT a viable substitute — the broken pieces are load-bearing primitives (boot, install, microcode, SSH host keys), not optional features.
- **Top 5 Tier-0 ship-blockers:** (1) microcode triple-failure stack; (2) signed-boot-and-shim chain (self-signed shim ships, install-gui/install-tui UKIs never signed by canonical script, no MS-shim sponsorship filed); (3) installer-cannot-install cluster (parted missing, shim staging fails, post-install hooks no-op, empty-archive-dir produces "successful" zero-package install, no initramfs, MOK ordering broken); (4) SSH host-key reuse across every shipped ISO + root SSH on by default + hardcoded `root:intergenos` password baked into shadow; (5) ffmpeg-nonfree + Qwen-license redistribution exposure (P-015 / P-016 — flagged in audit scope but underpopulated by WC; assumed v1.0 ship-blockers per directive).

---

## Tier 0 — v1.0 SHIP-BLOCKERS

### CLUSTER T0-1 — Microcode triple-failure stack

**Findings:** F-001, E-001, E-002, F-005 (Holy-Grail x4)
**Lead owner:** SPOC (build-system + kernel)
**Supporting:** IGOSC (cross-check on chroot + create-image side)
**Dependencies:** None — independent surgical fixes; verifiable on a single rebuild.

**Remediation summary.** Three independent layers all fail simultaneously, so ANY one fix in isolation produces no visible improvement. Layer 1 (F-001): `create-image.sh:292` tests `/usr/bin/iucode_tool` but the binary is at `/usr/sbin/iucode_tool` — one-line path fix; the whole Intel-microcode early-load block was silent-skipping. Layer 2 (E-001): `CONFIG_MICROCODE=y` is set but neither `CONFIG_MICROCODE_INTEL` nor `CONFIG_MICROCODE_AMD` is asserted, so olddefconfig leaves both at `n` — kernel refuses to consume any microcode initrd even when one is present. Layer 3 (E-002): `build-uki.sh:77` invokes ukify with a single `--initrd=$INITRAMFS` argument, so even if E-001+F-001 land, the kernel sees only the main initramfs and never the ucode cpio. Add a `scripts/build-microcode-initrds.sh` helper that emits `intel-ucode.img` + `amd-ucode.img` from `/usr/lib/firmware/{intel,amd}-ucode/` and pass each as additional `--initrd=` to ukify (ucode first, then main initramfs). F-005 closes the AMD side: add `packages/core/amd-ucode/` shipping linux-firmware's `amd-ucode/` blobs; extend create-image.sh + UKI helper to generate `amd-ucode.img` and surface it on both paths.

**Verification approach.** `dmesg | grep -E 'microcode: (early|sig=|revision=)'` on a freshly booted live ISO must produce vendor-specific output on both Intel and AMD hosts (verify in two test VMs with different vCPU vendor passthrough). M-020 adds the smoke check that turns this into a continuous gate. Pair with `objdump -h igos-live.efi` showing `.initrd` PE section sized for ucode+main, not just main. Pair with `grep -E 'CONFIG_MICROCODE_(INTEL|AMD)' /boot/config-*` returning `=y` for both.

**Estimated wall time.** 4-8 hours of coordinated build-rebuild-verify; one full chroot rebuild + one ISO rebuild + two VM boots.

**Owner decisions needed.** None — this is unambiguous correctness.

---

### CLUSTER T0-2 — Signed-boot + shim + mirror chain

**Findings:** B-001 (Holy-Grail), B-002, B-005, B-025, B-026, B-041, B-042, B-043, A-001, C-003, F-024 (Critical/Holy-Grail). Adjacent: B-003, B-015, L-001, L-002, L-008 (per directive — WC lanes not yet populated).
**Lead owner:** SPOC (signing + bootloader)
**Supporting:** WC (mirror state — Lane L when populated), IGOSC (installer-side UX)
**Dependencies:**
- A-001 (orchestrator never invokes shim-signed package) must land BEFORE any shim chain repair can be tested end-to-end.
- A-002 (orchestrator never builds ISO) must land BEFORE the canonical build path can verify the signed-boot chain — until then the chain is exercised only by ad-hoc spoc-*.sh kickoffs whose outputs we already can't reproduce (B-018 / B-034 manifest-doesn't-match-ISO).
- B-002/B-025 (sign-release.sh glob misses install-gui/install-tui UKIs) blocks every other signing fix from being reliably tested.

**Remediation summary.** The cycle-5 ISO ships an InterGenOS-self-signed shim (B-001) — no MS UEFI CA chain — so real hardware with stock dbx rejects it before grub ever loads. Even if shim worked, sign-release.sh's glob misses install-gui + install-tui UKIs (B-002), so the canonical ceremony silently ships them unsigned despite ops doc 03 promising otherwise (B-025). Even if those signed, sign-release.sh itself doesn't enforce the patched-OpenSC path (B-005) so RSA-4096 PIV would fail at the first UKI. Even if signing worked, the installed-system boot chain is order-broken: vmlinuz is MOK-signed at install time (B-042) but MOK is enrolled only after a successful first boot through MokManager (which has no InterGenOS UX — B-043), AND `root=UUID=` with no initramfs (B-041) means the first boot can't even resolve root. **Required sequence:**

1. **A-001 / shim-signed wiring** — add `run_package "shim-signed"` to `chroot-build-core-extra.sh`; verify `/usr/share/shim-signed/{shimx64,mmx64}.efi` lands.
2. **A-002 / orchestrator ISO phase** — wire `phase_squashfs` + `phase_iso` after `phase_manifest` (gated `--iso`); retire QCOW2 path (A-028) or document split.
3. **C-003 / installer shim-staging** — install fails closed with clear error before partitioning when shim missing.
4. **B-002 + B-025 / sign-release.sh glob** — extend to `igos-install-*.efi` + add post-sign count assertion `count(output) == count(input)`.
5. **B-005 + B-004 + B-023 + B-049 / signing-script hardening** — all sign-*.sh scripts default to `/usr/local/lib/opensc-pkcs11.so`, port modulus-match guard, remove PIN-in-URI.
6. **B-026 + B-041 + B-042 / installed-system boot fix** — wire dracut (or mkinitcpio) into the chroot AND have Forge run it post-install before grub-mkconfig; OR switch to `root=PARTUUID=` and ship initramfs-less; ALSO sign vmlinuz with InterGenOS distro CA (vendor_db) at build time in addition to MOK at install time so the first-boot-before-enroll path doesn't dead-end.
7. **B-001 / shim path** — owner-decision dependency below.
8. **B-043 / MOK enrollment UX** — print MOK password to a persistent file + first-boot service prompts user, or pre-stage cert into shim vendor_db (F-024).
9. **B-003 + B-018 + B-034 / build provenance** — wipe stale debug `.signed` artifacts, regenerate manifest atomically with ISO, write per-build `build/cycleN/{unsigned,signed,iso}/` lineage.

**Verification approach.** End-to-end SB-on test on real hardware (or OVMF with `secboot_vm_profile.py` per M-008 extension): boot ISO → no shim-reject → grub loads → kernel loads → install completes → reboot → MokManager prompts → enrollment → vmlinuz loads → installed system reaches multi-user.target. Wire M-006 (signed-ISO-artifact test) as post-sign-release.sh gate, plus M-016 (create-image.sh fixture test for `intel-ucode.img` + ESP layout) and B-016 (build-iso.sh `sbverify` against `VENDOR_CERT` when `UNSIGNED_TEST=0`).

**Estimated wall time.** 2-3 weeks of focused work, BLOCKED on B-001 shim-path owner-decision. If shim-review (B-015 — PR slipped past 2026-05-15 target) is the path, add 6-12 weeks of external dependency for MS sponsorship.

**Owner decisions needed.**
- **B-001 SHIM PATH.** (a) Open shim-review PR immediately, ship v1.0 with the Fedora-extract piggyback shim (already packaged at `packages/core/shim-signed/`) in the interim — practical, ships immediately after A-001 wires it. (b) Open shim-review PR, do NOT ship until MS-signed shim returns — 6-12 week delay. (c) Stay on InterGenOS-self-signed CA, document mandatory user `mokutil --import` of CA cert as first action on every install — increases UX friction but eliminates external dependency. Recommendation pending owner ratification: **(a) + shim-review filed in parallel**, lowest-friction shippable path.
- **B-006 measured boot scope.** Either implement `systemd-cryptenroll` LUKS+TPM2 sealing for v1.0 OR drop "measured boot" framing wherever it appears in docs. Dual narrative cannot ship.
- **B-008 / B-026 installed-system UKI vs grub-loads-vmlinuz.** Decide canonical installed-system boot architecture (UKI parity with live ISO, OR traditional grub-loads-vmlinuz). Initramfs generator selection (dracut vs mkinitcpio) follows.

---

### CLUSTER T0-3 — Installer cannot install (Critical correctness baseline)

**Findings:** C-001, C-002, C-003, C-004, C-005, C-006, C-007, C-008, C-009, C-010, C-065, A-003, A-004 (downgraded), A-005, A-006, B-026, B-041 (Critical x12+).
**Lead owner:** SPOC (installer + chroot package set)
**Supporting:** IGOSC (post-install hook semantics + UX)
**Dependencies:**
- A-002 (orchestrator wires ISO) AND M-001 (real end-to-end install harness) unlock verification.
- C-021 (run_chroot pre-flight binary check) makes the parted-class regressions surface loudly instead of cryptically.

**Remediation summary.** The cycle-5 ISO declared "installer ready" cannot actually install on real hardware. C-001/A-003: `parted` binary missing from chroot — every partition operation dies. C-005: same package gap for `partprobe`. C-003: `bootloader.py` raises RuntimeError when staging `/usr/share/shim-signed/` (root cause = A-001). C-004: post-install hooks NEVER fire — `packages_dir=/var/lib/igos/packages` is a flat manifest dir of 765 text files, not the source tree `packages/<tier>/<name>/` that `run_post_install_hooks` walks; zero hooks discovered → silent no-op. C-005: `cp -a packages_dir target_pkg_dir` on retry corrupts the nesting. C-006: virtual_fs double-mounts leak on every install. C-007: `ai` package group is selectable in the TUI but undefined in `GROUPS`, so checking it installs nothing. C-008: install-time signing smoke check uses wrong field names (`prev_hash`/`this_hash`) vs `integrity.py`'s `prev`/`entry_sha256` — always fails, silently masks audit-log breaks. C-009: smoke framework lands under `site-packages/installer/smoke/` not `/usr/lib/intergenos/` per its own header — user can't invoke it. C-010: locale-conf written but `locale-gen`/`localedef` never invoked so the chosen locale doesn't exist on target → silent fallback to C. C-065: empty `archive_dir` propagates to `install_packages` returning `(0,0,[])` and orchestrator marks PHASE_PACKAGES complete on a zero-package install. A-005/A-006: xfsprogs + mdadm missing entirely (XFS root + software RAID unsupported). B-026: no initramfs generator wired (vmlinuz can't mount root). B-041: `root=UUID=` cmdline without initramfs = guaranteed first-boot failure.

**Sub-cluster ordering:**
1. **Package set** — land parted (A-003), partprobe (auto via parted), xfsprogs (A-005), mdadm (A-006), dialog (A-004 — optional after severity downgrade to Medium), ntfs-3g (A-008 / C-014), os-prober (C-054), dracut (B-026), gptfdisk (A-007).
2. **Chroot wiring** — extend `chroot-health-check.sh` with installer-required binary set (M-002); add `scripts/check-installer-runtime-deps.py` that greps `installer/backend/*.py` for `subprocess.run([...])` and asserts each binary is in the chroot.
3. **Installer code fixes** — C-003 pre-flight, C-004 packages_dir layout, C-005 cp idempotence, C-006 virtual_fs single ownership, C-007 GROUPS adds `ai`, C-008 field rename, C-009 install path, C-010 localedef invocation, C-065 fail-on-zero-package, C-021 binary-presence pre-flight.
4. **Boot architecture** — B-026 dracut wiring or PARTUUID switch (B-041), tied to T0-2 cluster owner decision.

**Verification approach.** Real end-to-end install harness per M-001: extend `installer/tests/secboot_vm_profile.py` to attach a blank target disk, autotype TUI through Confirm, reboot, run `pkm verify --fast` on installed root. M-002 chroot-binary-presence gate catches regressions in CI. M-005 unit-test floor for disks.py + bootloader.py + hooks.py.

**Estimated wall time.** 2-3 weeks (package authoring + Forge code + dracut integration + test harness). Concurrent with T0-2.

**Owner decisions needed.**
- Whether to ship with traditional initramfs (dracut) or PARTUUID-only no-initramfs path (lower friction but precludes LUKS / NVMe-rescue / LVM root). Couples to T0-2 architecture decision.
- Whether to keep alongside-install / NTFS-shrink primitives (C-014) or delete as v1.x deferred (currently Rule 21 stubs).

---

### CLUSTER T0-4 — SSH host-key + root-account integrity

**Findings:** F-002 (Holy-Grail), F-003 (Holy-Grail), F-004 (Holy-Grail), F-006 (Holy-Grail), F-008, G-004 (Holy-Grail, twin of F-002), G-021 (twin of F-003), F-009, F-010, F-013, F-015, F-016, B-030, F-007, C-050 (Holy-Grail).
**Lead owner:** IGOSC (security posture, since these are IGOSC's Lane F + G findings)
**Supporting:** SPOC (build-squashfs cleanup + chroot package edits)
**Dependencies:** None — each independently fixable; coordinate to avoid sequencing collisions on `openssh/build.sh`.

**Remediation summary.** Every shipped ISO contains identical SSH host keys (F-002 + G-004 + F-008): `core/openssh/build.sh:82` runs `ssh-keygen -A` in post_install, baking keys into the chroot; `build-squashfs.sh` doesn't strip them; `create-image.sh:280` regenerates again for qcow2 → every installed system shares fingerprints → MITM-trivial across the fleet, direct POWER RULE violation ("no dev keys in shipped ISO"). Pair with F-003: sshd default `PermitRootLogin yes`; F-004: hardcoded `root:intergenos` password baked into `/etc/shadow` via shadow/build.sh; F-009: installer enables sshd by default on every install. Net: every install opens root-SSH-over-network with known credentials and known host keys at first boot. F-006 adds tty2-root-autologin in live mode — physical-access defeat. F-010: no pam_pwquality / pam_faillock — unlimited brute force. F-013: MOK signing key stored unsealed with `-nodes`. F-015: per-install MOK keys at RSA-2048 (below NIST 2030 floor of RSA-3072). C-050: GUI accepts 1-char user/root passwords with no validation. F-007: forge polkit rule (grants unconditional YES to user `liveuser`) installs system-wide on every desktop install, so an attacker who creates a `liveuser` post-install gets passwordless root.

**Sub-cluster ordering:**
1. **Strip baked keys + creds.** F-002 / G-004: remove `ssh-keygen -A` from openssh/build.sh post_install; add `rm -f /etc/ssh/ssh_host_*_key*` + `rm -f /etc/shadow` (regenerate with locked root) to build-squashfs.sh cleanup. F-004: replace hardcoded `root:intergenos` with `usermod -p '*' root` (locked account). F-008: remove ssh-keygen -A from create-image.sh; rely on sshd.service `ExecStartPre` to generate per-install at first boot.
2. **sshd hardening.** F-003 / G-021: ship `PermitRootLogin no` (or `prohibit-password`); F-009: drop sshd.service from default-enable list (opt-in via Forge UI checkbox); F-016: ship curated `sshd_config` (MaxAuthTries 3, LoginGraceTime 30, mozilla-modern KexAlgorithms/Ciphers/MACs, ClientAliveInterval).
3. **PAM password policy.** F-010 / C-050: wire `pam_pwquality.so retry=3` + `pam_faillock.so` into `/etc/pam.d/system-auth` + `system-password`; ship `/etc/security/pwquality.conf` (min length 8 + non-alpha required); enforce strength at frontend AND backend (mirror C-050/C-051 MOK-password pattern).
4. **Live mode hardening.** F-006: replace tty2 root autologin with liveuser autologin (matching tty1) or gate behind `igos.live-root-debug=1` cmdline flag.
5. **Polkit scoping.** F-007: skip `49-intergenos-forge.rules` install on installed targets (live-ISO-only), or gate the rule's YES on `/proc/cmdline contains igos.mode=live`.
6. **MOK key hygiene.** F-013: provide TPM-sealed-MOK toggle; F-015: bump MOK_KEY_BITS to 3072 or 4096.

**Verification approach.** Smoke check on installed system (add to `installer/smoke/checks/signing.sh`): `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` fingerprint MUST differ from build-host's; `grep -E '^root:[*!]' /etc/shadow` (locked); `grep -E '^PermitRootLogin (no|prohibit-password)' /etc/ssh/sshd_config`; `systemctl is-enabled sshd` returns `disabled` unless user opted in; `grep pam_pwquality /etc/pam.d/system-auth`. Wire into `--release` smoke (M-010).

**Estimated wall time.** 3-5 days. Independent of T0-1/T0-2/T0-3.

**Owner decisions needed.**
- Password aging policy (F-023): ship chage defaults (e.g., max 365 days, warn 14) OR explicitly document "no password aging by design."
- v1.0 stance on MOK key TPM sealing (F-013) — couples to B-006 measured-boot decision.

---

### CLUSTER T0-5 — Legal / redistribution exposure

**Findings:** P-015 (ffmpeg-nonfree), P-016 (Qwen license) — per directive scope; WC Lane P not yet populated in tracker.
**Lead owner:** WC (legal/license)
**Supporting:** IGOSC (Qwen UX integration in intergen-welcome / setup), SPOC (build flags for ffmpeg)

**Remediation summary.** Two ship-blocker categories. (a) `ffmpeg-nonfree`: if ffmpeg is built with `--enable-nonfree` (which enables non-redistributable codecs like libfdk_aac), the resulting binary cannot be lawfully redistributed in a ship-able ISO; mainline distros (Debian, Fedora) split this into a non-shipped/non-free repo. Action: audit `packages/extra/ffmpeg/build.sh` for `--enable-nonfree`; if present, remove for the ship-able build and emit a separate user-side `ffmpeg-nonfree-helper` (mirror-tier download helper, akin to VS Code / Chrome). (b) Qwen model license: Qwen models (cited in `intergen` tier as a candidate) ship under the Tongyi Qianwen License which has commercial-use restrictions + acceptable-use clauses that require explicit user acceptance. Cannot bundle in the ISO without user-side click-through accept. Action: gate the Qwen-tier model behind `intergen setup` license-accept UX; default to a permissively-licensed model (Llama 3.x with its license + Mistral with Apache) for any auto-download path.

**Verification approach.** `grep -E 'nonfree|gpl-incompat' build/intergenos-*.iso.manifest` returns nothing; ffmpeg binary in ISO `ldd | grep -v nonfree`; model_manager.py refuses to auto-download Qwen unless `--accept-license` was passed; pre-squashfs audit grep over all package.yml license fields against an allowlist.

**Estimated wall time.** 1-2 days for ffmpeg; 3-5 days for Qwen UX gating including welcomer integration.

**Owner decisions needed.**
- Default model choice for `intergen setup --auto` if not Qwen (Llama-3.1-8B-Instruct vs Mistral-7B-Instruct-v0.3 vs Phi-3-Mini — license-permissive options).
- Whether ffmpeg-nonfree-helper script ships in mirror tier or stays out of repo entirely.

---

## Tier 1 — CRITICAL CORRECTNESS

### CLUSTER T1-1 — Orchestrator end-to-end completeness

**Findings:** A-002 (Critical — wired into T0-2 too), A-028, A-029, A-022, A-023, A-038, A-040, A-041, A-042, A-044, A-045, M-046, M-048.
**Lead owner:** SPOC (build system)
**Supporting:** None (SPOC-local).
**Dependencies:** A-002 must land before any other build-pipeline fix can be tested through the canonical path.

**Remediation summary.** The orchestrator never executes the path it documents. `phase_image` only produces QCOW2; `build-squashfs.sh` + `build-iso.sh` exist as separate scripts the operator runs via the 80+ ad-hoc `spoc-*.sh` / `run-*.sh` kickoff scripts that have accumulated in `build/` (A-022). The QCOW2 in `build/intergenos.qcow2` is 10 days old (A-028); `.build-phase` says `core` (A-029) — nobody has run the documented end-to-end pipeline recently. Each ad-hoc kickoff is a hand-curated rsync of source-of-truth paths into chroot, and history shows kickoffs that synced 4 of 7 canonical paths silently shipped stale content (M-048). Fix: (1) wire `phase_squashfs` + `phase_iso` after `phase_manifest` in `build-intergenos.sh`, gated `--iso` to preserve QCOW2-only path during transition (then retire QCOW2 entirely per A-028); (2) move/delete the 80+ kickoff scripts to `build/_archive/<date>/`; (3) author M-046 test asserting every declared phase has a corresponding chroot-build-*.sh + invoked by orchestrator; (4) author M-048 kickoff-coverage test asserting every remaining ad-hoc script syncs all 7 canonical paths.

**Verification approach.** `bash scripts/build-intergenos.sh --user christopher --iso` from a clean chroot produces a bootable signed ISO at a canonical path with no manual steps. M-046 enforces phase/script alignment per push. Operator-side smoke: M-026 cycle-validator gains real pass/fail assertions.

**Estimated wall time.** 1-2 weeks. Coordinates with T0-2/T0-3.

**Owner decisions needed.** Retire QCOW2 entirely (recommended per A-028) or keep dual-output? Plus disposition of the 80+ archived kickoffs.

---

### CLUSTER T1-2 — Service hygiene + systemd unit correctness

**Findings:** G-001, G-002, G-006, G-007, G-008, G-009, G-010, G-011, G-013, G-014, G-015, G-016, G-017, G-018, G-019, C-019, C-022, C-023, B-010, B-035, D-005, D-006, D-007, B-040.
**Lead owner:** IGOSC (Lane G systemd hygiene)
**Supporting:** SPOC (chroot-config + presets); coordinates with T2-1 (sysctl hardening).
**Dependencies:** None — independent unit-by-unit fixes.

**Remediation summary.** Systemd unit hygiene gaps across server packages cause silent runtime failures. G-001: 7 server packages (etcd / haproxy / influxdb / memcached / valkey / postgresql / apache-httpd) declare `ReadWritePaths=/run/<name>` with no leading `-`, no `RuntimeDirectory=`, no tmpfiles.d — systemd refuses unit start. G-002: mariadb path mismatch between `/run/mysqld` and `/run/mariadb`. G-006: dual network managers (systemd-networkd + NetworkManager) both enabled via different paths. G-007: `/etc/resolv.conf` missing from chroot, DNS dead on live boot. G-008: valkey built without `USE_SYSTEMD=yes` but unit is `Type=notify` → 30s timeout then failed. G-009: postgres/memcached/haproxy/nginx have no `Restart=` directive. G-010: no `journald.conf` → unbounded journal growth. G-011: apache-httpd path collision through `/var/run -> /run` symlink. G-013: 99-default-disable preset undoes per-package enables (atd, fcron, cups, avahi, bluetooth, sshd) leading to nondeterministic state depending on which path ran last. G-014: no NTP/timesyncd configuration. G-015: intergen D-Bus activation bypasses systemd unit. G-016/G-017: EnvironmentFile= without dash → fatal on missing file; nginx ReadWritePaths too broad. C-019: `forge-tui.service Type=oneshot` with no `TimeoutStartSec` → infinite hang on failure. C-022/C-023: serial-getty for ttyS0 unconditional; no swap. B-010: tty1 contention between firstboot animation + first-boot-greeter. B-035 / B-040 / D-006 / D-007: firstboot units mask phantom services or never fire.

**Remediation:** systematic walk of each unit per the per-finding fix; consolidate sysctl/journald drops in a single `intergenos-baseline-config` package; align preset policy (per-package `systemctl enable` calls retire in favor of `80-intergenos-enable.preset` extension).

**Verification approach.** `systemctl --failed` on freshly booted install returns empty; `journalctl -b -p err` returns no service-start errors; M-015 implements per-system extension to smoke-services.list so the smoke harness actually checks the desktop service set.

**Estimated wall time.** 1-1.5 weeks.

**Owner decisions needed.** None — these are correctness fixes; consolidate post-fix.

---

### CLUSTER T1-3 — First-boot UX + branded artifacts that don't exist

**Findings:** D-001, D-002, D-003, D-004, D-005, D-006, D-007, D-008, D-009, D-010, D-011, D-012, D-013, D-014, J-001, J-002, J-003, J-004, J-005, J-006, J-010, J-011, J-012, J-013, J-015, J-016.
**Lead owner:** IGOSC (Lane D + Lane J — first-boot UX + GUI/desktop integration)
**Supporting:** SPOC (tarball-generation scripts wiring into `phase_setup`).
**Dependencies:**
- D-003 (intergen-welcome tarball missing) BLOCKS J-001/J-004/J-005/J-006 remediation. The "remove divergent install-theming.sh write blocks; defer to package" plan is correct only after the canonical packages are buildable end-to-end (D-003/J-003).
- D-002 (greeter binary stub) requires owner ratification — owner directive recorded: delete the greeter entirely.

**Remediation summary.** The branded first-boot UX exists in `assets/` but ships nowhere. D-001: first-boot animation built + has a working .service in `assets/intergen-firstboot-drm/` but no `packages/desktop/intergen-firstboot/` package — chroot has no binary. D-002: greeter binary referenced by `intergenos-first-boot-greeter.service` does not exist — DELETE per owner directive. D-003: `intergen-welcome` package pins `file:///intergen-welcome-1.0.tar.xz` with a real sha256 but no tarball ships in `build/sources/` and no `build-intergen-welcome-tarball.sh` companion exists — igos-build halts on missing source; the entire branded first-boot UX ships in NO image. D-004: same gap for the 4 `intergenos-extensions-*` packages. J-003: same gap for `intergenos-theme` + `intergenos-default-settings` + the 4 extensions packages — 7 InterGenOS-authored desktop packages cannot build because no companion tarball-generator scripts exist. J-001/J-002/J-004/J-005/J-006/J-010: `install-theming.sh` (the qcow2 dev path that masks D-003/J-003 by writing into image directly) has multiple divergent writers that overwrite the canonical package paths once D-003/J-003 are resolved. J-007: `install-theming.sh` writes `/etc/nftables.conf` + systemd unit, out-of-scope for a theming script. J-011: previews/ ships empty. J-012: no GNOME polkit auth agent shipped. J-013: idle-lock policy upstream-default (15min auto-lock) on installed systems. J-016 / D-010: brand assets (logos at `assets/intergen-mark/`) never installed to `/usr/share/icons/hicolor/`.

**Sub-cluster ordering:**
1. **D-002 delete** — owner-ratified; close greeter.service + binary.
2. **D-003 + D-004 + J-003 tarball generators** — author `scripts/build-intergen-welcome-tarball.sh`, `scripts/build-intergenos-theme-tarball.sh`, `scripts/build-intergenos-default-settings-tarball.sh`, `scripts/build-intergenos-extensions-<cat>-tarball.sh` x4 (or one parametric `build-igos-pkg-tarball.sh` taking pkg + version). Wire into `phase_setup` like `build-forge-tarball.sh`.
3. **D-001 packaging** — create `packages/desktop/intergen-firstboot/`; ship binary + session wrapper.
4. **install-theming.sh divestment** — once D-003/J-003 land, delete divergent write blocks per J-001/J-002/J-004/J-005/J-006/J-010 plans.
5. **J-007 firewall extraction** — move nftables config to dedicated package; resolves T0-4 polkit-scoping adjacency.
6. **Brand assets + polkit-gnome** — D-010 / J-016 / J-012 packages.
7. **Idle-lock policy** — J-013 explicit policy decision in gschema overrides.

**Verification approach.** Boot installed system through to GDM greeter → user login → welcome wizard renders with previews and brand mark → completion writes done-marker → second login does not re-show wizard. Add `installer/smoke/checks/desktop.sh` per M-039 with `check_desktop_gdm_enabled`, `check_desktop_welcome_present`, `check_desktop_theme_loaded`.

**Estimated wall time.** 1.5 weeks. Sequencing-bound by D-003/J-003 landing first.

**Owner decisions needed.**
- icon-theme + cursor-theme + button-layout single-source-of-truth (J-008 / J-009): pick the canonical from gschema vs dconf, resolve duplicate authority.
- whether to keep all 11 GTK themes or trim to the 8 the welcomer offers (J-014).

---

### CLUSTER T1-4 — pkm correctness + repo trust chain

**Findings:** H-001, A-011, A-031, M-017, M-027, M-028, M-029, A-037.
**Lead owner:** WC (pkm — Lane H)
**Supporting:** SPOC (signing-ceremony output staging the keyring).
**Dependencies:**
- A-011 (empty `available` table) and H-001 (missing keyring) are precondition fixes before any update / repo / search flow can be tested.

**Remediation summary.** pkm is the canonical install/update path but key trust-chain primitives are absent. H-001: `pkm/repo.py:74` GPG_KEYRING=`/etc/pkm/trusted.gpg` doesn't exist in the chroot; `pkm update` fails. A-011: `available` table is empty (765 installed rows, 0 available rows) — `pkm search`, `pkm list available`, planned repo sync all broken. A-031: gnupg + sbsigntool present in chroot but no pre-staged release keyring. A-037: `phase_publish` references `pkm.repo.generate_index()` + `sign_index()` — may be Rule 21 stub; verify. M-017/M-027/M-028/M-029: pkm CLI surface, remover, repo download/sync paths are 0-test.

**Remediation:** generate `/etc/pkm/trusted.gpg` as a guaranteed side effect of the signing ceremony; ship `packages/core/intergenos-release-keyring/` with the public release key; wire `pkm sync` into post-image or first-boot to populate `available`; author the missing tests so the trust-chain primitives don't regress invisibly.

**Verification approach.** Fresh install: `pkm update` succeeds; `pkm search <pkg>` returns results; `pkm verify --fast` returns clean exit code 0; M-029 integration test for `RepoManager.sync` against a tmp HTTP server proves the consumer-side flow.

**Estimated wall time.** 1 week.

**Owner decisions needed.** Whether the release keyring ships in a separate "intergenos-release-keyring" package or baked into the `pkm` package itself.

---

### CLUSTER T1-5 — Test infrastructure rebuild (M-class consolidation)

**Findings:** M-001..M-056 (57 findings) + M-043a; the cluster collapses to 7 actionable initiatives:

1. **End-to-end install harness** (M-001/M-003/M-004/M-008/M-034/M-037/M-038): extend `secboot_vm_profile.py` to drive real installs against blank target disks, including MOK enrollment via SPICE keystrokes and per-phase cancel.
2. **Chroot-binary-presence enforcement** (M-002): `check-installer-runtime-deps.py` + extension to `chroot-health-check.sh` — the gate that would have caught T0-3's parted/iucode_tool class.
3. **Signed-ISO artifact verification** (M-006/M-016/M-020/M-036): `sbverify` walk + microcode runtime check + ISO loop-mount assertions, wired post-`sign-release.sh` and as `--release` smoke mode.
4. **pytest/coverage canonicalization** (M-012/M-013/M-014/M-025/M-042/M-047/M-049/M-055/M-056): `pyproject.toml` with `[tool.pytest.ini_options] testpaths = [...]`; `pythonpath` covers in-tree packages; coverage floor 60% for v1.0; `tests/README.md`; bats-or-shellspec for the 3 bash test scripts; CI workflow runs the suite.
5. **Stubs gate as standing CI** (M-009): `scripts/check-aspirational-stubs.py` — first as full-tree audit, then graduate to pre-push gate 9 + build-squashfs Step 4.6.
6. **Smoke-test hardening** (M-010/M-011/M-015/M-019/M-035/M-039/M-040/M-041): `--release` mode turns SKIPs into FAILs; per-check fixture suites; smoke-services.list extension; githook fixture suite; desktop.sh checks; check-public-content fixtures expanded.
7. **Per-module unit-test floor** (M-005/M-017/M-018/M-022/M-023/M-024/M-027/M-028/M-029/M-030/M-031/M-032/M-033/M-043/M-043a/M-044/M-045/M-050/M-051/M-052/M-053/M-054): prioritized authoring — start with `safety.py` (Holy-Grail), `disks.py`/`bootloader.py`/`hooks.py` (T0-3 owners), `pre-squashfs-audit.py` (Holy-Grail gate), `remover.py`, `repo.py`. Property-based tests via `hypothesis` for validators.

**Lead owner:** SPOC (Lane M)
**Supporting:** Every coordinator authors tests for code they own.
**Dependencies:** Coverage measurement (initiative 4) unblocks quantifying gaps.

**Verification approach.** CI workflow green; `pytest --collect-only -q | wc -l` matches expected count; coverage report >= 60% per Python package; M-026 cycle-5 validator gains real assertions; M-001 install harness completes on every PR touching installer/.

**Estimated wall time.** 4-6 weeks of background test authoring, parallelized across coordinators. Initiatives 1-3 are blockers for un-pausing ISO builds; 4-7 are continuous improvement.

**Owner decisions needed.** Initial coverage floor (60% for v1.0 vs higher); whether `--release` smoke gates ship-decision automatically.

---

## Tier 2 — HIGH SECURITY / HARDENING

### CLUSTER T2-1 — Kernel hardening + sysctl drop-ins

**Findings:** E-006..E-014, E-019..E-021, E-024..E-043 (KSPP-recommended class); F-011, F-012, F-020 (cmdline + sysctl drop-ins).
**Lead owner:** SPOC (kernel — Lane E)
**Supporting:** IGOSC (Lane F security posture + Lane G sysctl shipping coordination).
**Dependencies:** None — independent of T0-1 microcode fixes despite both touching kernel config.

**Remediation summary.** Coalesce all KSPP gaps + sysctl drop-ins into a single canonical `intergenos-baseline-config` (or per-tier) package shipping:

- **Kernel config overrides** to `99-intergenos-overrides.config`: RANDSTRUCT_FULL=y (E-006); INIT_ON_FREE_DEFAULT_ON=y (E-007); ZERO_CALL_USED_REGS, DEBUG_LIST, DEBUG_SG, BUG_ON_DATA_CORRUPTION, GCC_PLUGIN_LATENT_ENTROPY, GCC_PLUGIN_STRUCTLEAK_BYREF_ALL (E-008); GCC_PLUGIN_STACKLEAK=y (E-028); IA32_EMULATION=n + COMPAT_BRK=n (E-037); KEXEC_SIG_FORCE=y (E-010); HIBERNATION=n (E-011); LOCK_DOWN_KERNEL_FORCE_INTEGRITY=y (E-024); FW_LOADER_USER_HELPER=n (E-025); MODULE_UNLOAD=n (E-026); BT_DEBUGFS=n (E-039); BLK_DEV_LOOP/OVERLAY_FS/SQUASHFS/ISO9660_FS=y (E-019); AES_NI_INTEL=y (E-041); explicit RANDOM_TRUST_CPU=n + RANDOM_TRUST_BOOTLOADER=n (E-043).
- **Sysctl drop-in `/usr/lib/sysctl.d/99-intergenos-hardening.conf`** (F-012 + E-009 + E-014 + E-027 + E-031 + E-034 + Lane G coordination): `kernel.unprivileged_userns_clone=0`, `vm.unprivileged_userfaultfd=0`, `net.core.bpf_jit_harden=2`, `kernel.sysrq=0`, `kernel.perf_event_paranoid=3`, `kernel.kptr_restrict=2`, `kernel.dmesg_restrict=1`, `kernel.kexec_load_disabled=1`, `kernel.unprivileged_bpf_disabled=1`, `fs.protected_{symlinks,hardlinks,fifos,regular}=2`, `kernel.io_uring_disabled=2` (after owner decision E-030).
- **UKI cmdlines** (F-011 + F-020): append `lockdown=integrity slab_nomerge init_on_alloc=1 init_on_free=1 pti=on mitigations=auto,nosmt module.sig_enforce=1` to all three of `cmdline.{live,install-gui,install-tui}.txt`. Resolve E-017 (verbose live cmdline) at the same time.
- **PAM hardening** (F-010 + F-019): pwquality + faillock + curated limits.conf.
- **journald + resolved + skel** (F-021 + F-022 + F-029 + G-010 + G-014): persistent journal, DNSSEC=allow-downgrade + DNSOverTLS=opportunistic, umask 027.
- **Per-build verifier** (E-023 + E-029): `scripts/verify-kernel-config.py` asserts every `CONFIG_*` from overrides survives olddefconfig (NR_CPUS=512 silent drop is the proof-of-class).
- **Build-key cleanup** (E-012): `rm -f certs/signing_key.pem certs/signing_key.x509` in linux-kernel/build.sh post-install.

**Verification approach.** Boot installed system → `lsmod | grep -i userfaultfd` should refuse load when unprivileged caller invokes; `sysctl -a | grep '^kernel\.kptr_restrict' = 2`; `cat /sys/kernel/security/lockdown` shows `[integrity]`; `objdump -p igos-live.efi | grep SizeOfImage` (per past UKI lesson); M-020 microcode check; new `tests/kernel/test_override_survival.py` per E-023.

**Estimated wall time.** 1-2 weeks: kernel rebuild + verify + sysctl package + boot tests.

**Owner decisions needed.**
- E-030 io_uring: keep `=y` + sysctl `kernel.io_uring_disabled=2`, OR `=n` entirely. KSPP recommends off; systemd benefits; recommendation = sysctl middle-ground.
- E-040 BT_BREDR: keep classic Bluetooth or LE-only.
- E-018 SMT default: ship `mitigations=auto,nosmt` (Holy-Grail-aligned) or `auto` (preserve SMT throughput).
- E-032 LIVEPATCH=y but no infra: leave forward-compat or turn off until v1.x ships the infra.

---

### CLUSTER T2-2 — Firewall + network posture (Holy-Grail-adjacent)

**Findings:** G-005 (Holy-Grail), G-022, G-026, F-018, F-029.
**Lead owner:** IGOSC (Lane G)
**Supporting:** SPOC (chroot-build ordering + preset).
**Dependencies:** Coordinate with J-007 (move nftables config out of install-theming.sh) and T2-1 (sysctl drop-ins shipping mechanism).

**Remediation summary.** G-005: nftables ships `policy accept` on input/forward/output — the firewall is wide open on every install. Comment claims "Operators tighten" but preset markets this as "default-deny baseline." Holy-Grail says deny-by-default. Flip input + forward to `policy drop`; explicit accept for loopback + established/related + opt-in service surface; rewrite the preset comment. G-022: server services use `After=network.target` instead of `network-online.target` — startup races on bind-to-0.0.0.0. F-018: algif_aead blacklist is incomplete mitigation for CVE-2026-31431 — compile out the surface (`CONFIG_CRYPTO_USER_API_AEAD=n`) instead of relying on modprobe blacklist that doesn't honor in-kernel API. F-029: systemd-resolved DNSSEC/DoT off by default.

**Verification approach.** `nft list ruleset` on freshly booted system shows `policy drop` on input + forward; smoke check `check_network_default_deny` added to `services.sh`; `cat /sys/module/algif_aead/initstate` returns absent (module compiled out, not just blacklisted); resolved status shows DNSSEC active.

**Estimated wall time.** 3-5 days.

**Owner decisions needed.** Default-allowed service surface for v1.0 (loopback only? + opt-in sshd? + GNOME mDNS for printer discovery?).

---

### CLUSTER T2-3 — intergen AI assistant correctness + safety

**Findings:** I-004, I-005 (Holy-Grail), I-006, I-007, I-008, I-009, I-010, I-011, I-012, I-013, I-014, I-015, I-016, I-001, I-002, I-003, F-038, M-043, M-043a.
**Lead owner:** IGOSC (Lane I)
**Supporting:** SPOC (packaging + verify_paths sidecar — I-003).
**Dependencies:** None standalone, but T1-3 (intergen-welcome packaging) gates the surfacing of intergen UX.

**Remediation summary.** intergen is non-functional and unsafe-by-construction across multiple layers. I-004: `dbus_daemon.main()` never enters a main loop; the literal comment says "In production, the D-Bus main loop would run here" — Rule 21 stub at the most-critical surface. Process exits immediately after `start_service()`. CLI fallthrough at `cli.py:90-96` masks this via transient direct sessions. I-005 (Holy-Grail): model SHA256 verification is TOFU — all 3 tier models + embedding model have `sha256=""` and `verify_model()` records the hash on first download and returns True. Any MITM accepted blindly + locked in as trusted. Direct Holy-Grail supply-chain violation. I-006: user-service daemon writes to root-owned `/var/log/intergen/`, `/var/lib/intergen/data/`, `/var/lib/intergen/models/` (created mode 0755 root:root) — writes fail silently → fallback to `~/.local/share/intergen/intergen.log` for logs, but model storage at `/var/lib/intergen/models/llm/` has no fallback → user can never download a model. I-007: two divergent shell-safety classifiers (`safety.py` vs `tools/run_command.py`) disagree on mount/umount/sudo/chmod/chown — only run_command.py is wired; safety.py is dead misleading code. I-009: MOK keypair stored under `/var/lib/intergen/mok/` — couples Holy-Grail signing material with assistant userspace namespace. I-010: sentence-transformers + torch never installed → Layer 2 semantic intent matching permanently disabled, fall-back to keyword-only without notice. I-012: `systemctl --global enable intergen.service` runs at package install — every new user gets a failed-unit log entry on every login. M-043: 14 of 21 intergen modules untested (4000+ LoC including 902-LoC router.py + 524-LoC dbus_daemon + 262-LoC safety.py running as system D-Bus service). M-043a: BLOCKED_COMMANDS has no evasion tests.

**Sub-cluster ordering:**
1. **I-004 main loop fix** — without this nothing else matters.
2. **I-005 pin model SHA256s** — populate manifest hashes; reject empty.
3. **I-007 safety classifier consolidation** — pick one canonical, delete the other; add evasion tests (M-043a).
4. **I-009 MOK namespace migration** — move to `/var/lib/intergenos/mok/` (system namespace).
5. **I-006 ownership model decision** — owner-decision below.
6. **I-010 semantic layer decision** — owner-decision below.
7. **I-012 disable global auto-enable** — opt-in via welcomer.
8. **I-008 surface intergen in welcomer** — couples to T1-3.
9. **I-016 INTERGEN_MODEL_PATH integrity check or removal.**
10. **M-043 module-by-module test authoring** (safety.py first, then router.py, then memory.py).
11. **I-001 / I-002 / I-014 orphan deletes**, **I-003 verify_paths extension**, **I-011 / I-013 doc + config stubs**.

**Verification approach.** `systemctl --user is-active intergen.service` returns active after intergen setup; `intergen ask "test"` returns a response via D-Bus, not CLI fallthrough; `sha256sum ~/.local/share/intergen/models/llm/*.gguf` matches pinned manifest hash; new evasion tests in `intergen/tests/test_safety.py` exercise BLOCKED command variants.

**Estimated wall time.** 1.5-2 weeks.

**Owner decisions needed.**
- **I-006 service model.** (a) System service with PolicyKit-mediated user access, OR (b) move all state to per-user `~/.local/share/intergen/` + `~/.config/intergen/`. Current hybrid breaks at runtime.
- **I-010 semantic layer.** (a) Build sentence-transformers + torch as pkm packages, (b) `intergen setup` installs them into a user venv with consent, OR (c) drop semantic layer from shipping config and document keyword-only intent matching as v1.0 behavior.
- **I-015 Tier-3 model in live mode.** Refuse live-mode downloads of large models, or just warn?

---

### CLUSTER T2-4 — Defense-in-depth gaps not blocking ship

**Findings:** F-014, F-017, F-019, F-022, F-023, F-025, F-026, F-027, F-028, B-006, B-007, B-019, B-020, B-031, B-032, B-033, B-044, B-045, B-046, B-047, B-048, B-049, B-050, B-051, B-052, B-053, A-024, A-031.
**Lead owner:** IGOSC (Lane F) + SPOC (Lane B bootloader).
**Supporting:** WC (Lane K docs alignment for B-014/B-024/B-047 doc drift).

**Remediation summary.** Catch-all bucket of medium-severity hardening + signing hygiene + cleanup items. Highlights:

- **F-014** atd auto-enable removal; **F-017** GRUB_DISABLE_OS_PROBER=true default; **F-019** /etc/security/limits.d/00-intergenos.conf; **F-022** /etc/skel umask 027 + .ssh/ pre-create; **F-023** chage policy decision; **F-025** delete `tester` user from squashfs; **F-026** post-first-boot MOK enrollment verification + desktop notification; **F-027** sudo secure_path extension; **F-028** sbat.csv kernel/stub/shim entries.
- **B-006** measured-boot scope (couples to T0-2); **B-007** objcopy --dump-section mutates input (known footgun); **B-019** sign-bootloader.sh /tmp hardcoded path; **B-020** squashfs SHA file outside UKI signature envelope (threat-model doc); **B-031** ESP case-mismatch; **B-032** EFI variable cleanup on uninstall; **B-033** live initramfs has no LUKS/NVMe rescue capability; **B-044** generate_enrollment_password() dead code; **B-045** MOK PHASE non-idempotent on resume; **B-046** no GRUB superuser password; **B-047** doc/mok-enrollment.md trust-chain diagram mismatch with reality; **B-048** vmlinuz signing pre-check skips on ANY signature, not THIS install's MOK; **B-049** PIN in PKCS#11 URI in argv; **B-050** unsealed MOK key on disk; **B-051** shim cert-rotation 6-month-lead constraint; **B-052** GRUB_DISABLE_OS_PROBER triplicate authority; **B-053** OVMF VARS template mismatch with self-signed shim.
- **A-024** verify_paths to assert mode/owner for setuid binaries; **A-031** chroot keyring pre-staging (couples to T1-4).

**Verification approach.** Per-finding; aggregate via expanded smoke-test.sh `--release` mode.

**Estimated wall time.** 1.5-2 weeks of background work.

**Owner decisions needed.** Threat-model decisions on physical-access (B-046 GRUB password), TPM-sealed MOK (B-050 / F-013), rescue-medium scope (B-033).

---

## Tier 3 — STRUCTURAL DEBT

### CLUSTER T3-1 — Package set completeness + verify_paths coverage

**Findings:** A-009 (chrony), A-016 (network debug tools), A-017 (logrotate), A-018 (smartmontools / hdparm), A-019 (kexec-tools), A-020 (linux-firmware verify_paths undercount), A-027 (115+79=194 packages missing/empty homepage), A-021, E-015 (NVIDIA), E-016 (ZFS); B-038 (kernel.intergenos SBAT entry).
**Lead owner:** SPOC (Lane A) + WC (Lane P license-implication tracking for NVIDIA/ZFS).

**Remediation summary.** Standard distro package coverage gaps — chrony with NTS support, dnsutils/tcpdump/mtr/nmap/whois, logrotate, smartmontools, kexec-tools. NVIDIA + ZFS are licence-decision items (E-015/E-016) for v1.x via download-helper pattern. A-020/A-027 are verify_paths coverage breadth: 24% of packages lack workable homepage values; firmware-tier audit blind because most files aren't declared (sample-key approach + count assertion).

**Estimated wall time.** 1-2 weeks.

**Owner decisions needed.** Whether nmap / NVIDIA-open / ZFS-helper ship in base, extra, or mirror tiers.

---

### CLUSTER T3-2 — Documentation drift

**Findings:** A-023 (operations/05 false claim about orchestrator integration), B-014, B-024, B-047, C-048 (12/13-phase comment disagreement), C-049, C-061, C-062; K-* TBD (WC Lane K not populated).
**Lead owner:** WC (Lane K).
**Supporting:** Every coordinator for docs they own.

**Remediation summary.** Post-fix sweep across `docs/operations/`, `docs/signing-procedure.md`, `docs/mok-enrollment.md` to reflect post-remediation reality. Critical: B-014 + B-024 + B-047 are signing/boot-chain doc drift that misleads operators following the canonical ceremony; A-023 + C-048 are smaller-scope but representative.

**Estimated wall time.** 3-5 days post-T0/T1.

---

### CLUSTER T3-3 — Build-system reproducibility + hygiene

**Findings:** A-012, A-025, A-044 (Forge tarball known non-reproducible — verify + remediate or document), A-013-revisit, A-014-revisit, A-022 (80+ ad-hoc scripts), A-030, A-032..A-043, A-045, B-022 (ceremony.py untested).

**Remediation summary.** Non-reproducible tar invocations at 3 sites (A-012 `pkg-functions.sh:420`, A-025 `tracker.py:151` + `:756`, A-044 forge). Identical fix shape: `--sort=name --mtime=@${SOURCE_DATE_EPOCH:-0} --owner=0 --group=0 --numeric-owner --pax-option=delete=atime,delete=ctime`. Plus assorted orchestrator hygiene (A-032..A-045) + ceremony test framework (B-022).

**Estimated wall time.** 1 week.

---

## Tier 4 — POLISH

Single section — Low + Cosmetic only, batched at end. Includes A-039 (smoke-test ISO accumulation rotation), A-041 (hash-coded versions in run_package), A-043 (resolv.conf placeholder), B-019 / B-024 / B-036 / B-038 / B-039 / B-040 / B-051 / B-052 polish items not already in T2-4, C-026..C-049 low items, D-011 / D-013 / D-014, G-022..G-028, I-001 / I-002 / I-011 / I-013 / I-014, J-014 / J-015, M-040 / M-041 / M-053..M-056. **Lead owner:** distributed across coordinators. **Estimated wall time:** background, opportunistic.

---

## Cross-cutting themes

### Test infrastructure rebuild (T1-5 expansion)
Covered in T1-5; the seven initiatives are the v1.0 test-floor. M-001 install harness + M-002 chroot-binary-presence + M-006 signed-ISO verification are blockers for un-pausing builds.

### Stubs gate as standing CI
M-009 → `scripts/check-aspirational-stubs.py` graduates from periodic-audit → pre-push gate 9 → build-squashfs Step 4.6. Tonight's audit (121 findings) is the cost of absence; once gated, Rule 21 enforcement becomes mechanical.

### Fleet workspace MCP namespace
Per CLAUDE.md MCP-server identity rule: posting at `_-_InterGenOS_-_Code__*` from the Ubuntu host attributes to IGOSC. SPOC must use `_-_Ubuntu_-_Code__*` only. Recurring pitfall — the `mcp-host-server-validate.sh` hook mechanically enforces; this remediation plan is filed by SPOC via the Ubuntu MCP server only.

### Operational runbook accuracy (post-fix sweep)
Pair every T0/T1 fix with the corresponding doc update; do not declare a cluster closed until the doc reflects reality. Coordinate WC Lane K for the sweep.

---

## Owner decision queue

Decisions blocking remediation start (or substantively affecting scope):

1. **B-001 SHIM path.** Fedora-piggyback shim (immediate) vs MS-signed shim via shim-review (6-12 week external dependency) vs self-signed CA + mandatory user enrollment.
2. **B-006 measured-boot scope.** Implement `systemd-cryptenroll` LUKS+TPM2 sealing OR drop "measured boot" framing from docs.
3. **B-008 / B-026 installed-system boot architecture.** UKI parity with live ISO OR traditional grub-loads-vmlinuz. Initramfs generator (dracut vs mkinitcpio) follows.
4. **A-002 / A-028 QCOW2 retirement.** Retire QCOW2 entirely or keep dual-output transitionally.
5. **A-004 dialog package.** Add dialog or standardize on whiptail and document.
6. **C-014 alongside-install primitives.** Keep + complete (ntfs-3g + ntfsresize package) or delete as v1.x deferred.
7. **F-013 / B-050 MOK TPM sealing.** v1.0 ship-decision — couples to B-006.
8. **F-023 password aging policy.** Ship chage defaults or document "no aging by design."
9. **F-007 forge polkit rule scoping.** Gate by cmdline or rename liveuser to namespace-distinct unguessable identifier.
10. **G-005 default firewall service surface.** Loopback-only? Opt-in sshd? mDNS for printer discovery?
11. **E-018 SMT default** (`mitigations=auto,nosmt` vs `auto`).
12. **E-030 io_uring posture** (`=y` + sysctl restrict vs `=n` entirely).
13. **E-040 BT_BREDR posture** (classic BT vs LE-only).
14. **E-032 LIVEPATCH=y but no infra** (leave forward-compat or turn off until infra ships).
15. **I-006 intergen service model** (system service + PolicyKit OR per-user paths only).
16. **I-010 intergen semantic layer** (build deps as pkm packages, install via setup-time venv, OR drop).
17. **I-015 large-model live-mode policy** (refuse or warn).
18. **T0-5 Qwen default model substitute** if not Qwen for `intergen setup --auto`.
19. **T0-5 ffmpeg-nonfree** (mirror helper or out entirely).
20. **D-014 GDM session-type policy** (Wayland-only, Wayland-preferred, or upstream-default).
21. **J-008 / J-009 / J-014 theming canonical single-source-of-truth** (icon, cursor, button-layout, GTK theme set).
22. **B-015 shim-review PR timing** (filing was 2026-05-15 target, slipped 3 days; close gated items NOW or accept further slip).

---

## Recommended dispatch order

**Phase A — week 1 (immediate parallel kickoff after owner decisions land on items 1-5 above):**
- SPOC: T0-1 microcode triple-fix; T1-1 orchestrator phase_squashfs+phase_iso wiring (A-002); T0-2 ordering steps 1-2 (shim-signed package wiring + orchestrator ISO phase).
- IGOSC: T0-4 SSH host-key + root-account integrity (independent, no blocker); start T0-2 step 8 MOK enrollment UX in parallel with SPOC's signing-chain work.
- WC: T0-5 ffmpeg-nonfree audit + Qwen license-UX design; T1-4 pkm keyring staging coordination with signing ceremony; Lane K/L/N/O/P population to bring tracker coverage to 16/16 lanes.

**Phase B — weeks 2-3 (T0-2 + T0-3 land in parallel):**
- SPOC: T0-3 package-set authoring (parted/xfsprogs/mdadm/dracut/os-prober/ntfs-3g/gptfdisk); T0-2 steps 3-7 (signing-script hardening + installed-system boot fix); T1-1 orchestrator hygiene + ad-hoc kickoff archive.
- IGOSC: T0-3 installer code fixes (C-003..C-010 + C-065 + C-021); T1-2 service hygiene walk; T1-3 tarball generators (D-003 / D-004 / J-003).
- WC: Lane L mirror state finalization; T1-4 pkm correctness; T0-5 closure.

**Phase C — weeks 3-4 (verification + hardening):**
- SPOC: T1-5 initiatives 1-3 (install harness + binary-presence gate + signed-ISO verification); T2-1 kernel hardening + sysctl drop-ins; T1-1 M-046/M-048 tests.
- IGOSC: T1-3 first-boot UX packages + install-theming.sh divestment; T2-2 firewall + DNSSEC; T2-3 intergen correctness (I-004 + I-005 + I-007 + I-009).
- WC: T3-2 documentation drift sweep; T3-1 package-set completeness.

**Phase D — weeks 4-5 (re-baseline cycle-6 ISO):**
- All coordinators: re-run orchestrator end-to-end (no ad-hoc kickoffs); cycle-6 ISO produced via canonical path; M-001 harness drives real install through to `pkm verify --fast` clean.
- Decision point: ship-candidate? If yes, file shim-review PR (if not yet filed) and proceed to ceremony.

**Phase E — weeks 5-6 (release-prep):**
- Sign-release ceremony against cycle-6 ISO; `--release` smoke gates; M-006 + M-036 signed-ISO verification; release-key fingerprint published; SBAT entries cross-checked.

**Parallelism opportunities.** T0-1 / T0-4 / T0-5 are fully independent of each other AND of T0-2/T0-3 — fire all five in week 1 if owner decisions land. T2-* clusters can start mid-Phase-B opportunistically.

---

## Verification gates

Gates wired post-remediation to prevent regression:

1. **Pre-push gate 9 — Rule 21 stubs scan** (M-009). Graduated from `scripts/check-aspirational-stubs.py` audit → pre-push gate.
2. **Pre-squashfs Step 4.5 — verify_paths audit** (already exists; extend per A-024 to assert mode/owner for setuid binaries).
3. **Pre-squashfs Step 4.6 — installer-binary-presence audit** (M-002 / `scripts/check-installer-runtime-deps.py`). Greps `installer/backend/*.py` + `scripts/create-image.sh` + `scripts/chroot-build-*.sh` for binary invocations; asserts each in chroot.
4. **Pre-squashfs Step 4.7 — Rule 21 stubs gate** (M-009, second graduation tier).
5. **Post-`sign-release.sh` gate — signed-ISO artifact verification** (M-006 + M-036). `sbverify` walk of all `.efi` in ESP against published cert; manifest fingerprint match; count-assertion (output `.efi` count == input).
6. **CI workflow — Python test suite** (M-013). `python3 -m pytest tests/ installer/tests/ intergen/tests/` on push to master + PR.
7. **CI workflow — shell test suite** (M-013 sibling). bats/shellspec for the 3 bash test scripts.
8. **CI workflow — kickoff-rsync coverage** (M-048). Greps every remaining `build/spoc-*.sh` + `build/run-*.sh` for rsync; asserts all 7 canonical paths covered.
9. **CI workflow — orchestrator phase completeness** (M-046). Parses `build-intergenos.sh` phases; asserts each maps to existing chroot-build-*.sh.
10. **Cycle-validator real assertions** (M-026). `virsh start` exit-code + `virsh domstate` + screenshot size + optional OCR.
11. **Smoke-test `--release` mode** (M-010). Conditional SKIPs become FAILs in release mode; `scripts/sign-release.sh` invokes with `--release`.
12. **Boot smoke microcode positive check** (M-020). `dmesg | grep -E 'microcode: (early|sig=|revision=)'` returns output on Intel and AMD hosts.
13. **Coverage floor** (M-012). 60% for v1.0 baseline; tighten to 80% for v2.0.
14. **Kernel-override survival check** (E-023 / E-029). `scripts/verify-kernel-config.py` asserts every `CONFIG_*=y/=m` from `99-intergenos-overrides.config` matches the final `.config` post-olddefconfig.

These gates collectively close the regression classes that produced this audit's findings.

---

*End of remediation plan.*
