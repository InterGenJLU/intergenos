# Stub-hunt audit — installer/* + admin scripts + pkm/ — 2026-05-15

Lane scope per 14:56Z dispatch: `installer/*` (backend, frontend,
data, init, iso, smoke, tests), admin scripts in `scripts/*.sh` and
`scripts/*.py` outside the chroot-build / image-pipeline lanes, and
`pkm/` source tree.

Authored by the installer-lane agent on thread `stub-hunt-2026-05-15`.

## Verification matrix

Format: file:line | claim text | on-disk reality | verdict.
Verdicts: PASS / STUB-FIXED / STUB-FLAGGED / FALSE-POSITIVE.

### A. installer/frontend — closed

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| A1 | tui.py:347-370 (pre-fix) | `disks.list_candidates()` returns disk list with `.size_gb` | Backend has `disks.detect_disks()` returning Disk with `.size_human`; `list_candidates` does not exist; `.size_gb` does not exist. The try-block always raised AttributeError so the except-fallback (text input) was the only reachable path. | STUB-FIXED in `af27b671` (rewrote to call `detect_disks()` with text-input fallback when empty) |
| A2 | gui/screens/disk.py:8-12 docstring | "TUI's `prompt_install_io` does the same for now (with a `disks.list_candidates()` fallback to text input)" | TUI now calls `detect_disks()` (A1 fix); docstring described stale function name | STUB-FIXED in `af27b671` (docstring repoints at `detect_disks()`) |
| A3 | gui/state.py:47-48, 187-188 | `install_mode: str = "fresh"` + `alongside_partition: Optional[str] = None`; forwarded to `io["install_mode"]` when ≠ "fresh" | No UI screen exposes a fresh-vs-alongside choice; both frontends are text-disk-path only. `backend/install.py:299` calls `disks.partition_disk()` unconditionally and never reads `install_io["install_mode"]`. Backend primitives `partition_disk_alongside` + `detect_shrinkable_ntfs` + `shrink_ntfs` exist but are imported nowhere downstream. | STUB-FLAGGED (architectural — pending helm decision; recommend lie-fix for v1) |

### B. installer/tests — closed

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| B1 | tests/class2b_boot_order.py:19 docstring | "documented TODO at bootloader.py:189" | `installer/backend/bootloader.py` contains zero TODO comments (grep TODO returns nothing); line 189 is the partition-number split. The actual soft-fail path is the rc != 0 fallback at bootloader.py:202-218. | STUB-FIXED in `af27b671` (rewrote to reference the actual rc != 0 fallback) |
| B2 | tests/README.md:84 | same false TODO claim | Same as B1. | STUB-FIXED in `af27b671` |
| B3 | tests/class2_runtime_sb_state.py:17 docstring | UEFI boot-order check "DEFERRED — separate concern, belongs in the Class 2b … probe (TODO)" | Class 2b is implemented at `installer/tests/class2b_boot_order.py`. The (TODO) parenthetical is stale. | STUB-FIXED in `f8d3f82d` (replaced parenthetical with `class2b_boot_order.py` path) |
| B4 | tests/test_grub_check_signatures.py:208-234 | Two test methods (`test_enforce_refuses_pecoff_kernel`, `test_noenforce_boots_pecoff_kernel`) with bodies that are `self.skipTest("Phase A-2 skeleton")` only | Skeleton tests with explicit `skipTest` + docstring enumerating missing plumbing (disk-image-aware grub.cfg write path, serial console capture, parser for grub error output). | FALSE-POSITIVE — explicitly-labeled documented-skeleton with skipTest is honest about its placeholder status; not a hidden stub. |

### C. installer/data — closed

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| C1 | data/forge-tui.service:11 ExecStart= | `/usr/bin/forge --mode tui --archives ... --packages ...` | `/usr/bin/forge` installed by `packages/desktop/forge/build.sh` after `28efc5a8` shipped. `--mode tui` accepted by `installer/__main__.py:131-135`. `/var/lib/igos/{archives,packages}` populated by pkm + build-intergenos.sh. | PASS |
| C2 | data/intergenos-first-boot-greeter.service:13 ConditionPathExists | `!/etc/intergenos/first-boot-completed` | Path is the success-flag the greeter writes at completion. Idempotency-by-design. | PASS |
| C3 | data/first-boot-greeter (script) | references `chpasswd`, `getent`, `mktemp`, `date`, `mkdir`, `mv` | All standard utilities provided by shadow + glibc + coreutils; FLAG_PATH writes to `/etc/intergenos/`. | PASS |
| C4 | data/install-schema.yaml | comments referencing `/var/lib/forge/install.yaml` | Frontend writers (`tui.py:310`, `gui/state.py:155`) both `parent.mkdir(parents=True, exist_ok=True)` before write. | PASS |
| C5 | data/49-intergenos-forge.rules + org.intergenos.forge.policy | (new files this arc) | Polkit rule + policy installed by forge build.sh's polkit step; verify_paths now covers both (post `f6afb038`). | PASS |

### D. installer/backend — closed

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| D1 | backend/install.py:221 docstring | install_io documents optional `install_mode` field | Same architectural stub as A3 — documented but never read by orchestrator. | STUB-FLAGGED (rolled into A3 fix scope) |
| D2 | backend/integrity.py | hash-chained JSONL audit log at configurable `audit_log_path` | Implemented; path is `/var/log/igos-integrity-override.log` from frontend INTEGRITY_AUDIT_LOG constants; `copy_audit_log_to_target` writes to same path on target. | PASS |
| D3 | backend/{install,hooks,packages,users,config,mok,bootloader,disks,_validators}.py path literals | various `/usr/share/zoneinfo/{tz}`, `/var/lib/intergen/mok`, `/etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service`, etc. | All resolve to standard LFS/BLFS package install targets or runtime-created paths (mok dir created at install time). | PASS |
| D4 | backend/bootloader.py:37 `SHIM_SOURCE_DIR = "/usr/share/shim-signed"` | shim-signed package install path | shim-signed has `pending_acquisition` exemption per Rule 20. Backend code handles absence gracefully via the rc != 0 fallback path. | PASS (pending-acquisition gracefully handled) |
| D5 | backend/config.py:105 `stub-resolv.conf` | path string contains "stub" | Legitimate systemd-resolved filename; not Rule 21 stub-pattern. | FALSE-POSITIVE |

### E. installer/smoke — partial

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| E1 | smoke/checks/signing.sh:75 (pre-fix) | `/var/lib/igos/audit/integrity-events.jsonl` is the install-time audit log | Real path is `/var/log/igos-integrity-override.log` (D2). Path mismatch caused the check to always silently `check_skip`. | STUB-FIXED in `47771b9e` |
| E2 | smoke/checks/signing.sh:35-41 | `/var/lib/igos/manifest/{intergenos-archive-manifest.txt,intergenos-release-key.asc}` exist on the installed system | The signed manifest + key live at `/install/...` on the LIVE ISO per `build-intergenos.sh:1083` comment ("embeds the signed manifest + release-key public component in the ISO at /install/..."). Forge install backend does NOT copy them to the installed target; smoke check on installed systems always `check_skip`s. | STUB-FLAGGED (architectural — choose: drop check, repoint to live-only path, or add install-time copy of manifest+key to `/var/lib/igos/manifest/`) |
| E3 | smoke/checks/{boot,services,pkm}.sh path refs | various `/boot/efi/EFI`, etc. | Standard EFI install layout; bootloader.py creates. | PASS |
| E4 | smoke/lib.sh + smoke-test.sh | check_pass / check_fail / check_skip / check_warn helpers | Stable interface; used uniformly across signing.sh + others. | PASS |

### F. installer/iso/grub/grub.cfg — closed

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| F1 | grub.cfg | three UKI chainload targets at `/EFI/InterGenOS/igos-{live,install-gui,install-tui}.efi` | UKIs produced by `scripts/build-uki.sh` and placed on ESP at the expected paths. Theme path guarded by `if [ -e ]`. | PASS |

### G. installer/init/build-initramfs.sh — closed (out-of-scope per dispatch but adjacent)

Not audited in detail in this lane; covered by the image-pipeline lane (`33b003eb` confirms init.sh:50 was the only finding there).

### H. pkm/ — closed

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| H1 | pkm/cli.py:250 + installer.py:425 | `/usr/bin/igos-install-*` helpers | 8 helper packages exist in `packages/extra/*-helper/` (vscode, brave, chrome, edge, discord, spotify, claude-code, go-md2man) — all `install -Dm755` the binary. | PASS |
| H2 | pkm/database.py:14-16 | `/var/lib/igos/{pkm.db,packages,archives}` | pkm package's build.sh creates `/var/lib/igos/{packages,archives}` (lines 57-58); `pkm.db` is created at first invocation. | PASS |
| H3 | pkm/repo.py:70 `/etc/pkm/repos.conf` | pkm shipped config | pkm build.sh:35 cat-writes `/etc/pkm/repos.conf` with the intergenos-current repo stanza. | PASS |
| H4 | pkm/repo.py:74 `/etc/pkm/trusted.gpg` | GPG keyring path | No in-tree creator; populated after signing-key ceremony (placeholder trust-store per pkm build.sh:48-49 "real keyring lands post-signing-key ceremony"). Treated as opt-in `gpg_verify` flag in repos.conf. | PASS (deliberately-staged for ceremony; not a stub since pkm gracefully handles absence) |
| H5 | pkm/installer.py:318 "not yet registered. Proceeding" | runtime log message | Informational message in a code path that proceeds with installation; not aspirational claim. | FALSE-POSITIVE |

### I. Admin scripts (scope: sign-* / host-check / audit-* / check-* / preflight-* / helpers) — closed

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| I1 | scripts/sign-*.sh | `/usr/local/lib/opensc-pkcs11.so`, `/tmp/*` | Signing-workstation environment; ceremony runbook owns provisioning. Not in build chroot. | PASS |
| I2 | scripts/audit-package.py:445-454 "placeholder" comments | JSON schema slots filled by human review | Audit-data document fields; explicit human-fill-in markers, not Rule 21 stubs. | FALSE-POSITIVE |
| I3 | scripts/preflight-tier-coverage.py:82 "not yet wired top level" | Comment describing the auditor's detection pattern | Self-describing detector, not aspirational claim about non-existent feature. | FALSE-POSITIVE |
| I4 | scripts/build-uki.sh:54, chroot-build-bootloader.sh:61 `systemd-stub` | systemd-stub binary used by UKI build | "stub" is the systemd builtin binary name (systemd-stub); not Rule 21 stub-pattern. | FALSE-POSITIVE |
| I5 | scripts/{publish-repo.sh, setup-githooks.sh, download-theming.sh, refresh-worktree-against-head.sh, check-manifest-signature.sh, ...} | Various standard paths + utility refs | All resolved to standard utilities + in-tree files; no aspirational claims. | PASS |

## Summary

**Total claims audited (mid-pass count):** 30+ rows across A-I.

**Verdicts mid-pass:**
- 19 PASS
- 6 STUB-FIXED (4 commits: `af27b671`, `f8d3f82d`, `f6afb038`, `47771b9e`)
- 2 STUB-FLAGGED (A3 = D1 architectural; E2 architectural) — pending helm decision
- 5 FALSE-POSITIVE (acceptable patterns)

**Architectural decisions needed from helm:**

1. **A3 / D1 — install_mode/alongside_partition dead code.** Recommend lie-fix (delete frontend collection + docstring mention) for v1. Reality-fix (wire orchestrator to alongside flow) would slip v1 ship.
2. **E2 — smoke manifest-signature check.** Path mismatch between live-media `/install/...` and post-install `/var/lib/igos/manifest/...`. Three options: drop the check on installed systems; repoint to live-only path; add install-time copy step. Recommend the install-time-copy option since smoke tests are post-install by design and the manifest preserves trust evidence.

**Out of scope handed back per lane boundaries:** image-pipeline scope to the parallel lane; chroot-build-*.sh to the build-orchestration subagents; docs/ to the docs lane.
