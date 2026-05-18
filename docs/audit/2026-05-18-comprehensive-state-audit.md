# InterGenOS — Comprehensive State Audit

**Started:** 2026-05-18 (Monday night CDT)
**Trigger:** Cycle-5 ISO presumed ready for signing; subsequent stubs sweep revealed significant gaps (Holy-Grail-class microcode regression, installer-can't-actually-install bugs, multiple Rule 21 stubs across systemd units / shell / Python / packages). Owner directive: **hard halt on ISO builds until this tracker is exhaustively compiled and every item remediated.**
**Coordinator:** SPOC
**Participating coordinators:** SPOC, IGOSC, WC. Other coordinators sidelined per owner.

---

## Scope — 16 lanes

| Lane | Title | Owner |
|---|---|---|
| A | Build system + chroot completeness | SPOC |
| B | Bootloader + Secure Boot chain | SPOC |
| C | Forge installer end-to-end | SPOC |
| D | First-boot + post-install UX | IGOSC |
| E | Kernel + drivers + firmware | SPOC |
| F | Security posture (Holy Grail filter) | IGOSC |
| G | Network + systemd service hygiene | IGOSC |
| H | pkm end-to-end functionality | WC |
| I | InterGen AI integration | IGOSC |
| J | GUI / desktop integration | IGOSC |
| K | Documentation drift | WC |
| L | Repository / mirror state | WC |
| M | Test coverage gaps | SPOC |
| N | Disk / partitioning capabilities | WC |
| O | Update mechanism | WC |
| P | License / legal compliance | WC |

---

## Schema — every finding row

```
| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
```

- **ID**: `<LANE>-<NNN>` (e.g., `A-001`, `F-014`). Stable forever.
- **Severity**: `Holy-Grail` | `Critical` | `High` | `Medium` | `Low` | `Cosmetic`
- **Title**: one-line summary
- **Description**: what's broken/missing, why it matters
- **Code refs**: `path:line` (markdown links preferred)
- **Remediation plan**: one-sentence direction (not necessarily the full diff)
- **Status**: `open` → `in-progress` → `fixed-pending-verify` → `closed`
- **Verified**: commit sha + on-disk validation result, or empty

## Severity rubric

| Tier | Definition | Examples |
|---|---|---|
| **Holy-Grail** | Violates Holy Grail security alignment — affects the security posture of every install | Microcode never loading; signed-boot chain missing; SSH host key reuse; default-password account |
| **Critical** | Breaks installer / boot / core functionality on real hardware | parted missing; bootloader stage fails; kernel won't load |
| **High** | Feature non-functional or significantly degraded; user-visible failure | pkm GPG keyring missing; service won't start; intergen daemon fails to connect |
| **Medium** | Workaround exists; degraded UX; non-critical drift | UX hint dropped; redundant code paths; documentation drift |
| **Low** | Polish, hygiene, defensive | Vestigial comments; orphaned dead source; cosmetic |
| **Cosmetic** | No functional impact | Typos, formatting, naming consistency |

## Conventions

- **Each agent edits only their own section below.** No cross-section edits during scan. Avoids merge conflicts; synthesis is SPOC's job at end.
- **Append-only during scan.** Don't reclassify existing findings; flag for revisit instead.
- **Don't deduplicate across lanes during scan.** Same issue surfacing in two lanes = signal, not noise. Synthesis collapses duplicates.
- **Iterate.** First pass: surface findings. Second pass: cross-check against actual code + classify false positives. Third pass: what did the first two passes miss?
- **Use sub-agents aggressively.** Owner directive: as many parallel sub-agents per lane as feasible.
- **Commit + push your section every iteration.** Don't hoard findings locally. SPOC pushes synthesis commits.

## Status header (update as agents complete iterations)

| Agent | Lanes | Initial scan | Iteration 2 | Iteration 3 | Notes |
|---|---|---|---|---|---|
| SPOC | A, B, C, E, M | in-progress | | | |
| IGOSC | D, F, G, I, J | dispatched | | | |
| WC | H, K, L, N, O, P | dispatched | | | |

---

## SPOC findings

### Lane A — Build system + chroot completeness

_(SPOC sub-agent population)_

### Lane B — Bootloader + Secure Boot chain

_(SPOC sub-agent population)_

### Lane C — Forge installer end-to-end

_(SPOC sub-agent population; seeded with tonight's Python sweep findings)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| C-001 | Critical | parted binary not in chroot | `installer/backend/disks.py:131-153,277,282,311` calls parted; binary missing from chroot entirely; no package builds it | [disks.py:131](../../installer/backend/disks.py#L131) | Add `packages/base/parted/` package; standard autotools build; verify chroot post-build | open | |
| C-002 | Critical | partprobe missing (ships with parted) | Alongside-install path dies on `partprobe` invocation; same root cause as C-001 | [disks.py:318](../../installer/backend/disks.py#L318) | Resolved by C-001 once parted package lands and includes partprobe | open | |
| C-003 | Critical | shim-signed staging fails unconditionally | `installer/backend/bootloader.py:37-39` references `/usr/share/shim-signed/{shimx64,mmx64}.efi`; directory doesn't exist; install raises RuntimeError at line 133 | [bootloader.py:37](../../installer/backend/bootloader.py#L37) | Add pre-flight check in install.py that detects missing shim and surfaces clear error BEFORE partitioning; band-aid until MS sponsorship | open | |

### Lane E — Kernel + drivers + firmware

_(SPOC sub-agent population)_

### Lane M — Test coverage gaps

**Inventory (pass 1, 2026-05-18):** 38 Python test files (`test_*.py`) across 6 trees:
`tests/` (16 files: pkm, preflight, igos_build, installer, repo-publish, upstream-check, sbom, download-sources), `installer/tests/` (16 files: class1–6, mok, integrity, install_orchestrator, gui_state, gui_yaml, tui_flow, post_install, grub, verify_sources), `intergen/tests/` (2 files: tools, integration), 1 fixture `branding/marks/final/test_small_sizes.py` (UI-only).
Shell tests: 3 (`installer/smoke/smoke-test.sh`, `tests/sbat/test_check_sbat_generations.sh`, `tests/manifest/test_manifest_phase.sh`).
CI: a single GitHub workflow (`.github/workflows/public-content-audit.yml`) that runs only `scripts/check-public-content.py` — no test suite invocation, no pkm/installer/igos-build CI coverage.
No `pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`, or `noxfile.py` in the repo; no coverage.py invocation anywhere.
Local gates: `.githooks/pre-push` (8 gates: force-push block, public-content scan, stale-master, bash/python syntax, scope vs message, conventional commit, co-author trailer, commit-msg audit, new-package verify_paths) and `scripts/pre-squashfs-audit.py` invoked from `scripts/build-squashfs.sh:255`.

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| M-001 | Critical | Install smoke "passes" without exercising partition / mount / bootloader on real disk | `installer/tests/test_install_orchestrator.py` mocks every backend at the orchestrator's import path (`installer.backend.install.{disks,bootloader,mok,packages,hooks,config,users}`). The orchestrator's 12-phase pipeline never invokes a real `disks.partition_disk`, `bootloader.install_bootloader`, etc. Cycle-5 "3-lane validation" (`build/spoc-final-3lane-validation.sh`) drives 4 `KEY_ENTER` presses through dialog menus to YAML emit, snaps a screenshot, destroys the VM — never calls `run_install()`. This is precisely why C-001/parted-missing wasn't caught: no test path in tree ever asks "does the chroot contain the binaries our installer shells out to?" | [test_install_orchestrator.py:184-300](../../installer/tests/test_install_orchestrator.py#L184), [spoc-final-3lane-validation.sh:60-72](../../build/spoc-final-3lane-validation.sh#L60) | Build an end-to-end install harness that drives the orchestrator against a real qcow2 target in a libvirt VM (`installer/tests/secboot_vm_profile.py` is the skeleton — extend to (a) attach a blank target disk, (b) `virsh console` autotype the TUI through to `Confirm install`, (c) reboot into installed root and run smoke-test.sh). Mark cycle-5 validator as "boot smoke only" in its banner. | open | |
| M-002 | Critical | No test verifies installer-required binaries exist in chroot | `scripts/chroot-health-check.sh` checks 30+ binaries (`gcc`, `gnome-shell`, `gdm`, `curl`, `git`, `ssh`, …) but never `parted`, `partprobe`, `mkfs.ext4`, `mkfs.fat`, `mkswap`, `iucode_tool`, `dracut`, `mokutil`, `efibootmgr`. The installer's runtime dependencies are documented nowhere as a machine-readable list, and no test cross-references the chroot manifest against the set of binaries that `installer/backend/*.py` invokes. Both C-001 (parted) and F-001 (iucode_tool path mismatch) belong to the same regression class: "the installer/image pipeline calls a binary that the chroot does not contain or contains at a different path." | [chroot-health-check.sh:141-250](../../scripts/chroot-health-check.sh#L141), [installer/backend/disks.py:131](../../installer/backend/disks.py#L131) | Add `scripts/check-installer-runtime-deps.py` that greps `installer/backend/*.py` + `scripts/create-image.sh` + `scripts/chroot-build-*.sh` for `subprocess.run(["X",…])` / bare-binary invocations, builds the set, and verifies each against the chroot (and against package.yml `verify_paths` declarations). Wire into `chroot-health-check.sh` and into `build-squashfs.sh` as a Step 4.4 gate alongside the pre-squashfs audit. | open | |
| M-003 | Critical | Install-TUI lane smoke stops at YAML emit | `build/spoc-final-3lane-validation.sh:60-72`'s install-tui lane drives 4 `KEY_ENTER` presses through the dialog flow then snaps `after-yaml-emit` — it captures evidence that the TUI accepted defaults and wrote a YAML, NOT that the YAML survived `validate_install_inputs()` or that `run_install()` ran. The same lane in `test_tui_flow.py` (`test_emit_and_read_roundtrip`, `test_backend_imports_load`) only tests the data structure / module load — no flow test asserts the TUI's emitted YAML actually drives a complete install. False confidence is structural: "cycle-5 passed 3 lanes" means "3 boot lanes produced screenshots", not "1 install lane completed". | [spoc-final-3lane-validation.sh:60-72](../../build/spoc-final-3lane-validation.sh#L60), [test_tui_flow.py:127](../../installer/tests/test_tui_flow.py#L127) | (a) Rename the lane to `install-tui-yaml-emit-only` in the cycle script's labels. (b) Add a real install lane: TUI through to Confirm, then `Yes`, then poll for `pkm verify --fast` PASS on the installed root via the libvirt serial console. | open | |
| M-004 | Critical | Install-GUI lane smoke is also boot-only | Same script, GUI lane: boot to greeter, snap T20/T30, snap tty2 (root autologin) — no test asserts any GUI screen advances past welcome, no test enters partition/user/confirm screens, no test asserts gui_state transitions match the design doc. All `test_gui_state_transitions.py` (24 tests) and `test_gui_yaml_accumulation.py` (24 tests) are pure unit tests on `installer/frontend/gui/state.py` and YAML emission — they never spin a GTK process. | [spoc-final-3lane-validation.sh:51-58](../../build/spoc-final-3lane-validation.sh#L51), [test_gui_state_transitions.py:36](../../installer/tests/test_gui_state_transitions.py#L36) | Add a `pytest-gtk` (or `dogtail`-driven) test that exercises the screen graph end-to-end in Xvfb/Wayland headless, OR add a libvirt-driven SPICE/VNC test using `xdotool` to drive screens to Confirm. Mark `install-gui` lane in cycle script as `install-gui-greeter-boot-only` until either lands. | open | |
| M-005 | High | Zero direct unit tests for 7 of 8 installer backend modules | Of `installer/backend/{disks,bootloader,config,hooks,packages,users,_validators,install,mok,integrity}.py` (10 modules, 2106 LoC), only `install.py` (orchestrator), `mok.py`, `integrity.py`, and `packages.py` have direct test files. `disks.py` (446 LoC — partitioning, mounting, NTFS shrink, BitLocker detection), `bootloader.py` (258 LoC — shim staging, grub install, EFI boot entry), `config.py` (191 LoC), `hooks.py` (251 LoC), `users.py` (104 LoC), and `_validators.py` (117 LoC) have NO direct unit tests. Only path: the orchestrator's mocked tests touch the modules' import paths, which guarantees nothing about their behavior. | [installer/backend/](../../installer/backend/) | Author `tests/installer/test_{disks,bootloader,config,hooks,users,_validators}.py` — each module is small enough (<450 LoC) that 15-25 focused unit tests per module is realistic. Prioritize `disks.py` (partitioning) and `bootloader.py` (shim staging) — these are where C-001/C-002/C-003 sit. | open | |
| M-006 | High | No test of the signed-boot chain on real artifacts from a built ISO | `tests/manifest/test_manifest_phase.sh` exercises the manifest signing flow with synthetic archives + ephemeral GPG key (good), and `installer/tests/test_class1_integration.py` signs/verifies a stand-in `ext4_x64.efi` refind driver as a PE32+ proxy (also good). NEITHER test runs against the actually-built signed UKI/shim/grub artifacts from a real ISO. `installer/tests/test_grub_check_signatures.py` has a `test_enforce_refuses_pecoff_kernel` test, but it depends on a pre-staged `/var/lib/libvirt/images/intergenos-target.qcow2` that doesn't auto-populate. There's no CI gate that says "if a release-signed ISO was just produced, verify all its load-bearing PE binaries against the published key." | [test_class1_integration.py:34-49](../../installer/tests/test_class1_integration.py#L34), [test_grub_check_signatures.py:59](../../installer/tests/test_grub_check_signatures.py#L59) | Add `tests/iso/test_signed_iso_artifacts.py` that, given `build/intergenos-*.iso`, extracts the squashfs, walks `/boot/efi/EFI/intergenos/{shimx64,grubx64}.efi` + `/boot/EFI/Linux/*.efi` (UKIs), and `sbverify`s each against the published release cert. Wire into post-build of `scripts/sign-release.sh`. | open | |
| M-007 | High | Reproducibility test exists for shim only — not for whole-ISO or per-package | `scripts/verify-b2-reproducibility.sh` compares two independent shim builds bit-for-bit (excellent). No equivalent for: (a) the full ISO (two clean builds at the same commit should produce identical squashfs.sha256 modulo signature/timestamp); (b) any individual package other than shim; (c) the UKI assembly. Without these we can't make hermetic-build claims about anything but shim. | [verify-b2-reproducibility.sh](../../scripts/verify-b2-reproducibility.sh) | Generalize to `scripts/verify-package-reproducibility.sh <pkg>` that diffs two pkm tarballs of the same package + version, and `scripts/verify-iso-reproducibility.sh` that diffs two ISOs at the same commit. Mark known non-reproducible artifacts (signatures, embedded timestamps) and exclude with explicit rationale per the existing shim script's pattern. | open | |
| M-008 | High | No test of MOK-enrollment end-to-end (mokutil → reboot → enrolled) | `installer/tests/test_mok.py` and `installer/tests/test_class5_module_sigs.py` test MOK keypair generation + module-sig kernel state in isolation. `installer/smoke/checks/signing.sh:check_signing_mok_enrolled` validates post-install. There is NO test of the full flow: Forge stages MOK → reboot → shim MOK Manager prompts → user enters password → MOK enrolled in firmware → kernel secondary keyring populated. The handoff between Forge stage and shim's `MOK Manager` (which requires manual keypress at the firmware) is the highest-risk untested surface in the boot chain. | [installer/backend/mok.py](../../installer/backend/mok.py) | The libvirt+OVMF+swtpm harness in `installer/tests/secboot_vm_profile.py` is close to this — extend to drive shim's MOK Manager via SPICE keystrokes (the `KEY_*` codes from `spoc-final-3lane-validation.sh` show this is feasible), then assert against the `.secondary_trusted_keys` keyring in the rebooted target. | open | |
| M-009 | High | Pre-squashfs audit has no sibling Rule 21 stub gate (R3 not landed) | `docs/operations/10-recommendations.md:R3` recommends `scripts/check-aspirational-stubs.py` — grep `init.sh` / `*.service` / `*.desktop` / `tmpfiles.d` / `sysusers.d` / polkit / dbus configs for path references and cross-check against package install manifests. Not yet authored. Tonight's stub-hunt audit (4 agents, 121 findings → ratified D-001, D-002, G-001, G-002, G-003, I-001, I-002, I-003, J-001) is the cost of absence. Without this gate, Rule 21 enforcement is reactive — someone notices the stub during audit rather than at PR time. | [10-recommendations.md:R3](../../docs/operations/10-recommendations.md) | Author `scripts/check-aspirational-stubs.py`. Start as periodic full-tree audit (low-stakes, false-positive-tolerant). Graduate to pre-push gate 9 once tuned. Run it as a build-squashfs Step 4.6 gate (sibling to the verify_paths audit at Step 4.5). | open | |
| M-010 | High | Smoke-test.sh "passes" on missing manifest with SKIP, not FAIL | `installer/smoke/checks/signing.sh:check_signing_manifest_signature` SKIPs (not FAILs) when `/var/lib/igos/manifest` is absent. For a v1.0 release-signed install this directory MUST exist; SKIP gives false confidence that signing is working when actually no manifest was even produced. Same false-confidence pattern at `check_signing_audit_log` (SKIP if no log), `check_signing_mok_enrolled` (SKIP if no MOK cert), `check_signing_secondary_keyring` (WARN on permission denied — could mask absent keyring). | [installer/smoke/checks/signing.sh:36-83](../../installer/smoke/checks/signing.sh#L36) | Add `--release` mode to smoke-test.sh: turn the conditional SKIPs into FAILs when invoked with `--release`. `scripts/sign-release.sh` post-step and ISO acceptance tests would run with `--release`; dev iteration without. | open | |
| M-011 | High | smoke-test.sh has no test of its own checks (no fixture-driven regression suite) | `installer/smoke/checks/{pkm,signing,boot,services}.sh` have ~25 `check_*` functions total. Zero tests of the checks themselves: there's no fixture-driven suite that asserts `check_boot_dmesg_clean` correctly FAILs on a planted `BUG:` line, that `check_pkm_verify` correctly translates pkm's exit codes 0/1/2/N, that `check_signing_audit_log` correctly detects a broken hash chain. Symmetric to `tests/check-public-content/run-tests.sh` for the public-content audit. Risk: a refactor of the check functions silently breaks them without any signal at PR time. | [installer/smoke/checks/](../../installer/smoke/checks/) | Author `tests/smoke/test_*.sh` — one per check module, fixture-driven (mock `dmesg` / `mokutil` / `pkm` / `systemctl` via PATH-overridden shims). Same shape as the public-content test runner. | open | |
| M-012 | Medium | No test coverage measurement (no coverage.py, no `--cov` flag anywhere) | No `coverage`, `pytest-cov`, `.coveragerc`, or coverage report artifact in the tree. Impossible to answer "what % of installer/backend/disks.py is exercised by its (currently nonexistent) tests" without standing up the infrastructure. Without baseline coverage numbers, M-005 and M-001 can't be quantified — we know there are gaps, we can't measure the trend. | (no file — infrastructure gap) | Add coverage measurement: `pip install pytest-cov`, author `pyproject.toml` (or `setup.cfg`) with `[tool.pytest.ini_options]` + `[tool.coverage.run]` configured for `installer/`, `pkm/`, `igos-build/`. Add CI workflow `.github/workflows/python-tests.yml` that runs the suite + emits `coverage.xml`. Target floor: 60% for v1.0, 80% for v2.0. | open | |
| M-013 | Medium | No CI runs the Python test suite (only public-content audit) | `.github/workflows/public-content-audit.yml` is the only CI workflow. The 38 Python test files, 3 shell test scripts, pre-push gates 3 (syntax checks) and 8 (verify_paths) — none of these run on push/PR in CI. A regression that breaks `tests/pkm/test_verifier_modes.py` is detectable only by an agent who happens to run `python3 -m unittest` locally; pre-push doesn't run tests either. | [.github/workflows/](../../.github/workflows/) | Add `.github/workflows/python-tests.yml` running `python3 -m pytest tests/ installer/tests/ intergen/tests/` on push to master + PR. Add a separate `shell-tests.yml` for the three .sh test scripts. Tier by speed — fast unit tests on every push, slow integration (`test_class1_integration.py` — requires sbsign on the runner) gated behind a label or weekly schedule. | open | |
| M-014 | Medium | Test infrastructure language inconsistent — pytest, unittest, bash all in tree | Mixed conventions: most Python tests are `unittest.TestCase` subclasses (`tests/pkm/test_verifier_modes.py`, `installer/tests/test_*.py`), but some use bare `def test_*` + `pytest` fixtures (`tests/repo-publish/test_*.py`, parts of `installer/tests/test_tui_flow.py`). `tests/preflight/test_*.py` uses pytest. Shell tests use `set -euo pipefail` + manual fail-count accounting (3 different counter patterns across `check-public-content/run-tests.sh`, `tests/sbat/test_check_sbat_generations.sh`, `tests/manifest/test_manifest_phase.sh`). No `pytest.ini` or `pyproject.toml` to canonicalize collection rules. | (mixed across `tests/` and `installer/tests/`) | Pick ONE convention: pytest with bare functions, since unittest's `TestCase` API works under pytest's runner. Add a `pyproject.toml` with `[tool.pytest.ini_options] testpaths = ["tests", "installer/tests", "intergen/tests"]` so a single `pytest` invocation covers everything. Migrate the shell test counters into a shared `tests/lib/sh-test-lib.sh` (no duplicated accounting). | open | |
| M-015 | Medium | Smoke-test.sh `services.sh` hardcodes 4 critical services — no extension hook | `SMOKE_CRITICAL_SERVICES=(dbus systemd-journald systemd-logind systemd-udevd)` is hardcoded. The check explicitly says "Hard-coded for v1.0 (per ratified design Q3); `/etc/intergenos/smoke-services.list` extension is v1.0+1 backlog." That backlog item never landed. NetworkManager, gdm, intergen daemon, pkm timer — none get smoke-tested. Symmetric false confidence to M-010. | [installer/smoke/checks/services.sh:11](../../installer/smoke/checks/services.sh#L11) | Implement the v1.0+1 extension: read `/etc/intergenos/smoke-services.list` if present, fall back to the hardcoded baseline. Ship a default `smoke-services.list` in the `intergen-smoke-test` package with the real desktop service set. | open | |
| M-016 | Medium | No test of `scripts/create-image.sh` (ISO builder itself) | `scripts/create-image.sh` is 800+ lines that assemble the bootable ISO (microcode early-load — see F-001 — kernel + initramfs staging, grub.cfg generation, El Torito layout). It has no unit tests, no integration tests, no fixture-driven harness. F-001 (microcode never loading because of a `/usr/bin` vs `/usr/sbin` path check) is exactly the regression class that a simple "after running create-image.sh, assert `/boot/intel-ucode.img` exists in the iso loopmount" test catches. | [scripts/create-image.sh:292](../../scripts/create-image.sh#L292) | Author `tests/image/test_create_image.sh` that runs `scripts/create-image.sh` against a minimal staged tree fixture, then loop-mounts the produced ISO and asserts: (a) `intel-ucode.img` present, (b) `vmlinuz` + `initramfs` present, (c) `grub/grub.cfg` includes microcode early-load, (d) `EFI/BOOT/BOOTX64.EFI` exists. Wire as `--strict` smoke for release ISOs. | open | |
| M-017 | Medium | `pkm` end-to-end install test does not exist | `tests/pkm/test_verifier_modes.py`, `test_database_supersedes.py`, `test_installer_supersede.py`, `test_supersede_failure_paths.py` — all good unit tests of the database + verifier + supersede state machine. None exercise `pkm install <real package tarball from build/repo>` end-to-end against an actual chroot. `pkm/cli.py` (the user-facing entry point) has no direct test file. `pkm/repo.py:74` GPG_KEYRING path (`/etc/pkm/trusted.gpg`) — flagged as H-001 — would be caught by an integration test that runs `pkm update` against a real repo. | [pkm/cli.py](../../pkm/cli.py), [tests/pkm/](../../tests/pkm/) | Author `tests/pkm/test_install_e2e.py` that builds a minimal package tarball in tmpdir, points pkm at it, asserts the install lands files and DB row. Author `tests/pkm/test_cli_*.py` for the CLI surface (subcommand argparse, exit codes, error formatting). | open | |
| M-018 | Medium | `igos-build` orchestrator has no end-to-end test | `tests/igos_build/test_{builder,graph,parser_supersedes,tracker_b4_staging}.py` (50+ tests) cover individual modules well. No test runs `igos-build.py --build --tracked --skip-built --only <test-package>` against a real chroot and asserts the package built + DB updated + sidecar emitted. `verify_paths_derive.py` (sidecar producer) is referenced by the pre-squashfs audit but has no direct test. | [tests/igos_build/](../../tests/igos_build/) | Author `tests/igos_build/test_e2e_minimal_pkg.py` — a chroot fixture (or mocked chroot via fakeroot) that builds a one-file "hello" package and asserts on the produced tarball + DB row + auto-derived sidecar. | open | |
| M-019 | Medium | No fixture suite for the 8 pre-push gates themselves | `.githooks/pre-push` has 8 gates; only gate 1 (public-content audit) has a fixture suite (`tests/check-public-content/run-tests.sh`). Gates 0 (force-push detection), 2 (stale-master), 3 (syntax), 4 (scope-vs-message), 5 (conventional commit), 6 (Co-Authored-By), 7 (commit-msg audit), 8 (new-package verify_paths) have no tests. A refactor of the bash logic in any of these is unguarded. Cost-of-absence: gate 4's threshold (50 lines) and gate 6's threshold (25 lines) were both tuned by hand against historical incidents; no test pins them. | [.githooks/pre-push](../../.githooks/pre-push) | Author `tests/githooks/test_pre_push.sh` — fixture-driven per-gate, using a tmp-git-repo + scripted commits to trigger each gate's pass and fail paths. Same shape as the public-content test runner. | open | |
| M-020 | Medium | Intel/AMD microcode runtime test absent from smoke | `installer/smoke/checks/boot.sh:check_boot_dmesg_clean` greps for `BUG:` / `kernel panic` / `integrity:.*invalid` — does NOT grep for `microcode:` / `microcode revision` patterns that would prove iucode_tool actually loaded ucode at boot. F-001 (microcode never loading on shipped ISOs) would be caught by a positive check: `dmesg | grep -E 'microcode: (early|sig=|revision=)'` should produce output on Intel hosts. | [installer/smoke/checks/boot.sh:8-22](../../installer/smoke/checks/boot.sh#L8) | Add `check_boot_microcode_loaded()` to boot.sh: parse dmesg for vendor-specific microcode load patterns; PASS if a revision update is observed, WARN if the CPU is microcode-up-to-date already, FAIL if no microcode subsystem mentions appear at all. | open | |
| M-021 | Low | `intergen/tests/test_integration.py` requires a live llama.cpp server | The intergen integration suite (`runner.py`, `client.py`, `conversations.py` — 2000+ LoC) spins up a model + grades responses. Has no CI hookup. Risk: silent rot of the integration suite (conversation fixtures drift from the implemented tool surface) until someone runs it by hand. | [intergen/tests/test_integration.py](../../intergen/tests/test_integration.py) | Add a CI workflow gated on `[skip llm]` or a manual dispatch trigger, running against a tiny model on a CPU runner (slow but verifiable). Alternatively: mark `test_integration.py` as `@pytest.mark.requires_llm` and document the dispatch-only convention. | open | |
| M-022 | Low | TUI test suite is import-and-data-only by self-admission | `installer/tests/test_tui_flow.py` opens with "TUI testing is inherently limited: the dialog/whiptail subprocesses require a real terminal. These tests focus on: 1. Data structures and flows that can be exercised without a terminal. 2. YAML emit/parse round-trip correctness. 3. Abort cleanup behaviour. 4. Import sanity — all backend modules load without error." Honest scope statement, but the gap is real: no TUI test ever drives a real dialog subprocess. | [installer/tests/test_tui_flow.py:1-12](../../installer/tests/test_tui_flow.py#L1) | Use `pexpect` to drive `dialog`/`whiptail` in a pty — feasible and well-established (Debian's installer uses this pattern). Author `tests/tui/test_tui_e2e_pexpect.py` covering the 5-screen happy path. | open | |
| M-023 | Low | `intergen/tests/test_tools.py` validates the auto-allow list but not the deny list | 30+ `test_auto_*` tests in `test_tools.py` confirm that read-only commands (ls, cat, grep, ps, etc.) and confirm-required commands (mkdir, cp, mv, chmod, etc.) are routed to the correct policy bucket. No test asserts that a command NOT on either list is rejected (e.g., what does intergen's tool layer do with `dd if=/dev/urandom of=/dev/sda`?). Without an explicit deny-test, a regression that silently widens the auto-allow policy goes undetected. | [intergen/tests/test_tools.py:34-130](../../intergen/tests/test_tools.py#L34) | Add `test_unknown_command_rejected`, `test_destructive_no_silent_auto`, `test_disk_write_rejected` to lock the deny semantics. | open | |
| M-024 | Low | `_validators.py` in installer backend has zero coverage despite being a validation module | `installer/backend/_validators.py` (117 LoC) is the input-validation surface (password rules, hostname format, locale codes, etc.). Zero direct tests; orchestrator tests exercise validators only via the happy-path `validate_install_inputs` call. The validators are exactly the place where adversarial inputs (Unicode lookalike usernames, oversize hostnames, locale-injection attempts) should be tested — they're the trust boundary between user input and `subprocess.run` calls in the rest of the backend. | [installer/backend/_validators.py](../../installer/backend/_validators.py) | Author `tests/installer/test_validators.py` with property-based tests (`hypothesis`) for each validator: valid inputs accepted, malformed inputs rejected with explicit error messages, length/charset boundary cases enumerated. | open | |
| M-025 | Cosmetic | No test discovery convention documented anywhere | New contributor cannot answer "where do tests live?" or "how do I run them?" — there's no `tests/README.md`, no `CONTRIBUTING.md` test section, no `Makefile` target. They'd have to grep the tree. The split between `tests/` (top-level), `installer/tests/` (in-tree), `intergen/tests/` (in-tree), and `tests/installer/` (top-level with installer-shaped name) is itself confusing — two `installer/tests` paths. | (multiple) | Author `tests/README.md`: directory layout, naming conventions, how to run the full suite, how to run a single subtree. Add `Makefile` targets `make test`, `make test-fast`, `make test-coverage`. Consolidate `tests/installer/` into `installer/tests/` or vice versa to remove the two-paths-same-name footgun. | open | |

---

## IGOSC findings

### Lane D — First-boot + post-install UX

_(IGOSC sub-agent population; seeded with tonight's findings)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| D-001 | High | First-boot animation built but never shipped | `assets/intergen-firstboot-drm/` has working DRM/KMS binary + .service + Makefile; no `packages/desktop/intergen-firstboot/` package; chroot has no binary; greeter's `After=intergen-firstboot.service` is a no-op | [assets/intergen-firstboot-drm/](../../assets/intergen-firstboot-drm/) | Create `packages/desktop/intergen-firstboot/`; ship binary + session wrapper; wire via custom Wayland session that runs pre-compositor on first login | open | |
| D-002 | Medium | Greeter binary is a stub | `installer/data/intergenos-first-boot-greeter.service` references `/usr/libexec/intergenos/first-boot-greeter`; binary doesn't exist anywhere | [intergenos-first-boot-greeter.service:37](../../installer/data/intergenos-first-boot-greeter.service#L37) | DELETE the .service entirely (owner directive); installer handles user creation, no role for tty1 greeter | open | |

### Lane F — Security posture

_(IGOSC sub-agent population; seeded with tonight's finding)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| F-001 | Holy-Grail | Microcode never loading on shipped ISOs | `scripts/create-image.sh:292` tests `/usr/bin/iucode_tool` but binary is at `/usr/sbin/iucode_tool`; entire Intel microcode early-load block silently skipped; shipped images running with unpatched CPU vulns (Spectre/Meltdown/Zenbleed-class) | [create-image.sh:292](../../scripts/create-image.sh#L292) | One-line path fix; verify post-build that `/boot/intel-ucode.img` exists in shipped ISO; add post-build assertion | open | |

### Lane G — Network + systemd service hygiene

_(IGOSC sub-agent population; seeded)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| G-001 | High | 7 server packages have ReadWritePaths=/run/<name> without tmpfiles.d | etcd/haproxy/influxdb/memcached/valkey/postgresql/apache-httpd all declare RW paths under /run with no leading `-`, no RuntimeDirectory=, no tmpfiles.d entry; systemd refuses unit start if path missing at namespace setup | [packages/extra/{etcd,haproxy,influxdb,memcached,valkey,postgresql,apache-httpd}/](../../packages/extra/) | Add `tmpfiles.d/<name>.conf` to each package creating `/run/<name>` at boot; canonical systemd pattern | open | |
| G-002 | High | mariadb /run/mysqld vs /run/mariadb path mismatch | Service declares `ReadWritePaths=/run/mysqld`; package's own tmpfiles.d creates `/run/mariadb`; unit will fail at start | [mariadb.service:41](../../packages/extra/mariadb/mariadb.service#L41) | Pick one path; align both sides | open | |
| G-003 | Low | init.sh masks nonexistent apache.service | `init.sh:220-223` masks `apache.service`; chroot has `httpd.service` instead; no-op but Rule 21 noise | [init.sh:220](../../installer/init/init.sh#L220) | Drop the apache.service line from the mask list | open | |

### Lane I — InterGen AI integration

_(IGOSC sub-agent population; seeded)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| I-001 | Low | intergen/data/intergen.service is orphan dead code | Package build.sh generates its own inline replacement; source file misleading | [intergen/data/intergen.service](../../intergen/data/intergen.service) | Delete the orphan; build.sh inline is authoritative | open | |
| I-002 | Low | intergen/data/com.intergenos.InterGen.service same orphan class | Same as I-001 | [intergen/data/com.intergenos.InterGen.service](../../intergen/data/com.intergenos.InterGen.service) | Delete the orphan | open | |
| I-003 | Medium | ai/intergen verify_paths undercount | Declares 2 paths; build.sh installs ~10 (CLI wrapper, config, systemd user unit, dbus service, state dirs) | [packages/ai/intergen/package.yml](../../packages/ai/intergen/package.yml) | Extend verify_paths to cover load-bearing entry points | open | |

### Lane J — GUI / desktop integration

_(IGOSC sub-agent population; seeded)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| J-001 | Medium | install-theming.sh writes divergent intergen-welcome path | Writes script to `/usr/share/intergen-welcome/` with stale-Exec autostart; canonical package installs to `/usr/libexec/intergen-welcome/`; bypasses package's once-per-user gate | [install-theming.sh:397](../../scripts/install-theming.sh#L397) | Remove divergent write block from install-theming.sh; defer entirely to package | open | |

---

## WC findings

### Lane H — pkm end-to-end functionality

_(WC sub-agent population; seeded)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| H-001 | High | pkm GPG keyring missing | `pkm/repo.py:74` GPG_KEYRING=/etc/pkm/trusted.gpg; doesn't exist in chroot; pkm update fails | [pkm/repo.py:74](../../pkm/repo.py#L74) | Generated as side effect of signing ceremony; verify exists post-ceremony and during install | open | |

### Lane K — Documentation drift

_(WC sub-agent population)_

### Lane L — Repository / mirror state

_(WC sub-agent population)_

### Lane N — Disk / partitioning capabilities

_(WC sub-agent population)_

### Lane O — Update mechanism

_(WC sub-agent population)_

### Lane P — License / legal compliance

_(WC sub-agent population)_

---

## Iteration log

| Date/time | Agent | Action |
|---|---|---|
| 2026-05-18 ~00:50 CDT | SPOC | Tracker created. Seeded with tonight's 4-agent stubs sweep findings. Lane assignments dispatched. |
| 2026-05-18 ~01:30 CDT | SPOC sub-agent (Lane M) | Pass 1: 25 Lane M findings populated. Inventory: 38 Python test files, 3 shell test scripts, 1 GitHub workflow (public-content only). Critical findings: M-001 (install smoke is mocked top-to-bottom), M-002 (no test of installer-required binaries in chroot — root cause class of C-001/F-001), M-003/M-004 (cycle-5 3-lane validation is screenshot-only), M-006 (signed-boot chain not tested on real ISO artifacts). |
