# Codebase Audit — §2 Installer

**Auditor:** chris-ubuntu_hplt-codium-deepseek  
**Date:** 2026-05-07 09:10–09:45 CDT  
**Master:** c4d5126  
**Paths audited:** `installer/` (backend, frontend, tests)  
**Findings:** 0 CRITICAL, 0 HIGH, 3 MEDIUM, 9 LOW  
**Test surface:** 301 passed, 17 skipped, 0 failures

---

## Summary

The installer is a well-structured dual-frontend system (TUI + GTK4 GUI) backed by a shared 11-phase orchestrator (`install.py:434 lines`). All 7 backend modules (`disks, config, users, packages, hooks, bootloader, mok, integrity`) have test coverage. The integrity verification module (Step 2 of the ship-gate, 433 lines, 36 tests) is clean: isolated GPG keyring for manifest signature, hash-chained JSONL audit log, regex-validated MOK common names. The orchestrator's exception handler correctly unmounts based on how far the pipeline progressed.

Findings are all MEDIUM or below — zero critical or high. The main areas are: database handle leak in packages.py, mount failure partial-state in hooks.py, and several minor error-reporting gaps.

---

## Closure Table

| ID | Severity | Section | File:Line | Finding | Proposed Fix |
|----|----------|---------|-----------|---------|--------------|
| I1 | MEDIUM | Installer | `installer/backend/packages.py:134,159` | `PackageDB` opened on line 134, used in loop (lines 140-157), but `db.close()` on line 159 is OUTSIDE any try/finally. If `PackageInstaller.install()` raises an uncaught exception mid-loop, the SQLite database handle leaks (file descriptor + potential WAL corruption). | Wrap lines 134-159 in `try: ... finally: db.close()`. Alternatively, make PackageDB a context manager. |
| I2 | MEDIUM | Installer | `installer/backend/hooks.py:20-22` | `mount_virtual_fs()` iterates 5 mounts with `check=True`. If mount #3 fails, mounts #1 and #2 are left mounted with no rollback. The orchestrator's exception handler cleans up via `_PHASES_NEEDING_VIRTFS_UNMOUNT`, but direct callers of this function get no partial-state protection. A retry-after-failure would hit "already mounted" errors on mounts #1 and #2. | Wrap in try/except that unmounts previously-completed mounts on failure: `try: for cmd, _ in reversed(completed_mounts): subprocess.run(["umount", mountpoint])`. |
| I3 | MEDIUM | Installer | `installer/backend/hooks.py:29-31` | `unmount_virtual_fs()` calls `subprocess.run(["umount", ...], capture_output=True)` but does NOT check return codes. Silent umount failure (e.g., device busy) means subsequent calls to `mount_virtual_fs()` fail with "already mounted" — the installer's exception handler then fails to remount on retry. | Check return code and log warning: `if result.returncode != 0: logger.warning(f"umount {path} failed (may be busy)")`. |
| I4 | LOW | Installer | `installer/backend/install.py:409-412` | `copy_audit_log_to_target` failure silently swallowed with `except Exception: pass`. Diagnostic info (why the copy failed — disk full? permissions?) is lost. The comment correctly says "don't fail the install" but there's no observability. | Add `_emit(PHASE_CLEANUP, 12, "warning: audit log not copied to target")` inside the except block. |
| I5 | LOW | Installer | `installer/backend/integrity.py:176` | `verify_manifest_signature` returns `False` on all errors: TimeoutExpired (gpg hung), FileNotFoundError (gpg not installed), OSError (permissions). Callers can't distinguish "signature is genuinely bad" from "gpg is not installed" — two very different diagnostics. | Return `Optional[str]` with None=valid, str=error message. Or add structured error: `(bool, Optional[str])` where the string carries the reason. |
| I6 | LOW | Installer | `installer/backend/config.py:46-55` | `generate_hostname` writes `${hostname}.localdomain` to `/etc/hosts` without hostname validation. The YAML schema should constrain hostnames, but this function is a defensive backstop. A hostname with `\n` could inject additional /etc/hosts lines. | Add `re.fullmatch(r'^[A-Za-z0-9]([A-Za-z0-9\-]*[A-Za-z0-9])?$', hostname)` validation. Reject if mismatch. |
| I7 | LOW | Installer | `installer/backend/disks.py:57-63` | `detect_disks()` calls `subprocess.run(["lsblk", ...])` without timeout. If `lsblk` hangs (e.g., NFS mount is stuck, broken storage driver), the installer blocks indefinitely at disk detection. | Add `timeout=30` to the subprocess.run call. |
| I8 | LOW | Installer | `installer/backend/hooks.py:148-151` | `cp -a` of `packages_dir` into target filesystem without checking return code. If the copy fails (disk full, permission error), `executed` stays at 0 but no error is reported — the installer reports success with 0 hooks applied. | Check return code: `if result.returncode != 0: _emit(PHASE_HOOKS, i, "warning: cannot copy packages — hooks skipped")`. |
| I9 | LOW | Installer | `installer/backend/install.py:393-399` | MOK enrollment is queued AFTER bootloader install but BEFORE cleanup (per design: "enrollment failure leaves system bootable"). However, if `queue_mok_enrollment` raises, the install reports FAILED even though the system IS fully functional (packages, users, bootloader all succeeded). The error message says "install failed" but the user can boot and re-enroll manually. | Consider catching `queue_mok_enrollment` failures and marking them as non-fatal warnings rather than install failures. Or add explicit guidance in the error message: "Install complete except MOK enrollment — system IS bootable; run mokutil --import manually after first boot." |
| I10 | LOW | Installer | `installer/backend/users.py:52-57` | `enable_sudo_for_wheel` uses string replace (`# %wheel` → `%wheel ALL=(ALL:ALL) ALL`). If the sudoers file has been customized or the distro shipped with a different comment format, the replace is a no-op and sudo is silently disabled. | Use `visudo -c -f` to check, then `echo '%wheel ALL=(ALL:ALL) ALL' | tee -a /etc/sudoers` as append, or use `sed` with a more robust pattern: `sed -i 's/^#\s*%wheel\s\+ALL=(ALL:ALL)\s\+ALL/%wheel ALL=(ALL:ALL) ALL/'`. |
| I11 | LOW | Installer | `installer/backend/mok.py:79-88` | `openssl req` command uses f-string interpolation without `shlex.quote()` on `common_name`. The CN is regex-validated at line 65, so this is NOT a security issue, but it's a code fragility — if someone weakens the regex or passes cn through another path, the injection vector opens silently. | Use `shlex.quote(common_name)` in the f-string even though the regex already guards it: defense in depth. |
| I12 | LOW | Installer | `installer/__main__.py:48` | `parse_cmdline_installer_mode` reads `/proc/cmdline` with `open()` in a try/except catching `(FileNotFoundError, PermissionError)`. No `OSError` catch for other I/O errors (e.g., `EIO` on broken kernel interface — unlikely but possible in container environments). | Add `OSError` to the except tuple. |

---

## Detailed Analysis

### A. Orchestrator (`install.py`)

434 lines, clean dataclass-based design. The 11-phase pipeline with `result.phase_completed` tracking and granular unmount cleanup is well-thought-out.

*Strengths:*
- `PHASE_VERIFY` inserted before any disk write — correct anti-supply-chain placement.
- `_PHASES_NEEDING_UNMOUNT` / `_PHASES_NEEDING_VIRTFS_UNMOUNT` provide granular cleanup based on exact phase reached.
- `VerifyConfig` dataclass cleanly separates integrity config from orchestrator flow.
- `REQUIRED_YAML_FIELDS` + `REQUIRED_INSTALL_IO_FIELDS` validated upfront in validate phase.
- `validate_install_inputs` aggregates ALL errors before raising — no one-at-a-time loop with the user.
- Dry-run mode via `disks.set_dry_run(True)` — correct global flag pattern.

*Weaknesses:*
- Broad `except Exception as e:` on line 419 is correct for the orchestrator (must catch anything), but `str(e)` may be unhelpful for some exception types. (LOW, protocol-level not code-level.)
- Audit log copy failure swallowed (I4).
- MOK enrollment failure treats a bootable system as a failure (I9).

### B. Integrity module (`integrity.py`)

433 lines, implements the install-time half of the anti-supply-chain ship-gate (Step 2 + 5 + 6a/6b).

*Strengths:*
- `verify_manifest_signature` uses throwaway keyring (no user keychain fallback) — correct security posture.
- `INTEGRITY_WARNING_TEMPLATE` is hardcoded with master fingerprint, not read from manifest — prevents manifest-controlled warning text attack.
- `OVERRIDE_PHRASE_FORMAT` per-package with normalized name — strong user-intent signal.
- `sha256_file` uses 64KB chunked reading — memory-safe for large archives.
- `parse_manifest` handles PGP clearsigned format (stops at `-----BEGIN PGP SIGNATURE-----`).
- `copy_audit_log_to_target` — hash-chained audit trail survives onto installed system.

*Weaknesses:*
- Error detail lost in signature verification (I5).
- Hardcoded master fingerprint in warning text becomes stale if key rotates — but this is design-intentional (the warning references the current release's fingerprint, not a durable URL).

### C. Backend Modules

**disks.py (429 lines)** — Disk detection + partitioning. Supports fresh and alongside modes. 
- `ALONGSIDE_MIN_ROOT_BYTES = 250GB` hardcoded — intentional per design doc.
- `is_efi()` checks `/sys/firmware/efi` — correct.
- No timeout on lsblk subprocess (I7).

**packages.py (160 lines)** — Archive discovery + pkm install dispatch.
- Supersede-aware: builds queue_names list and passes to `installer.install(name, ..., queue=queue_names)` for correct install ordering.
- **Database handle not in try/finally (I1)** — most actionable MEDIUM finding.

**config.py (191 lines)** — fstab, hostname, locale, vconsole, timezone generation.
- `_get_uuid` uses `lsblk -no UUID` — correct.
- Hostname validation missing (I6).

**users.py (81 lines)** — Root/user account creation.
- Password fed via stdin (`run_chroot_stdin`) not argv — correct for process-table hygiene.
- Sudoers string replacement brittle (I10).

**hooks.py (180 lines)** — Virtual FS mount/unmount, chroot execution, post-install hook orchestration.
- `run_chroot` and `run_chroot_stdin` clean abstractions.
- `run_post_install_hooks` scans for `post_install()` in build.sh — correct pattern.
- `mount_virtual_fs` lacks partial-failure cleanup (I2).
- `unmount_virtual_fs` lacks return-code checks (I3).
- `cp -a` without return-code check (I8).

**mok.py (221 lines)** — MOK keypair generation + enrollment.
- `_COMMON_NAME_RE` regex `^[A-Za-z0-9 _.-]{1,64}$` — strong shell-injection guard.
- `openssl req` CN insertion via single-quoted `-subj '/CN={}/'` — correct quoting with regex guard.
- No shlex.quote defense-in-depth on CN (I11).

**bootloader.py (267 lines)** — EFI installation + shim/GRUB signing.
- Imports `logging` — the only backend module using Python's logging instead of print/subprocess.

### D. Frontend

**tui.py (603 lines)** — Declarative-builder TUI using dialog(1). 
- 38 tests (test_tui_flow.py), all passing.
- TODO on disk detection logic (lines 338-339) flagged for SPOC follow-up.

**GUI (7 screens + state + style + window + integrity_dialog)** — GTK4/libadwaita 7-screen flow.
- Dedicated screens: welcome, keyboard_locale, disk, user, confirm, progress, done.
- `state.py` YAML builder accumulates user selections.
- `integrity_dialog.py` handles paste-disabled Gtk.Entry for override phrase.
- Test coverage via `test_gui_state_transitions.py` (191 lines) + `test_gui_yaml_accumulation.py` (336 lines).

### E. Test Suite

```
installer/tests/ — 23 test files, 301 passed, 17 skipped, 0 failures
├── test_integrity.py (607 lines, 36 tests) — comprehensive integrity testing
├── test_install_orchestrator.py (504 lines) — full pipeline tests
├── test_tui_flow.py — TUI declarative builder tests (38 tests, all pass)
├── test_mok.py — MOK keypair generation + enrollment tests (all pass)
├── test_gui_state_transitions.py + test_gui_yaml_accumulation.py — GUI tests
├── test_verify_sources.py — verify-sources phase tests (5/5 pass)
├── test_class1_chain_verify.py — Secure Boot chain verification (class 1)
├── test_class2b_boot_order.py — UEFI boot order verification (class 2b)
├── test_class2_runtime_sb_state.py — Secure Boot runtime state (class 2)
├── test_class5_module_sigs.py — Kernel module signature enforcement (class 5)
├── test_class6_apparmor_state.py — AppArmor enforcement state (class 6)
├── test_post_install_integration.py — post-reboot verification (17 skipped, vm-required)
└── test_grub_check_signatures.py + test_grub_output_parser.py — GRUB verification
```

17 skipped tests are for post-reboot verification that requires a running VM — correctly skipped on dev host.

All 7 backend modules have direct or indirect test coverage, confirmed by grep.

---

## Audit Techniques Applied

| Technique | Result |
|-----------|--------|
| Logic-flow tracing | Traced `run_install` 11-phase pipeline with error/recovery paths. PHASE_VERIFY insertion point correct. Cleanup granularity correct. |
| Error-handling scan | 6 except blocks: 1 broad catch in orchestrator (correct), 2 in integrity.py (typed), 1 in install.py (audit-log, bare — too broad), 2 cleanup catches (correct). |
| Hardcoded-path scan | `/mnt/target` (DEFAULT_TARGET), `/var/lib/forge/install.yaml` (YAML_PATH), `/var/lib/intergen/mok/` (MOK_DIR), `/var/lib/igos/archives` (archive_dir default). All are installer-canonical paths, not code smells. |
| Test gap analysis | All 7 backend modules have test coverage. 301 tests pass / 0 fail. |
| Shell robustness | N/A — installer is Python-only. Subprocess calls properly use lists (shell=False). |
| Missing dep declaration | `packages.py` imports `pkm` — confirmed present at repo root. All other imports are stdlib. |
| git-hygiene | 3 TODO comments in bootloader.py + tui.py — non-blocking, documented for SPOC follow-up. |
| Run tests first | `python3 -m pytest installer/tests/ -v` → 301 passed, 17 skipped, 0 failures in 2.51s. |
