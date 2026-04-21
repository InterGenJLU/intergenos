# Forge Secure Boot Test Harness

This directory holds the test harness for Forge SB — the Secure Boot signing
chain InterGenOS enforces end-to-end from UEFI through kernel. Each test class
answers a specific question about whether the chain is intact, and the harness
is partitioned so that tests requiring heavyweight infrastructure (`sbsign`,
`libvirtd`, a running VM) skip cleanly on machines that lack them.

## How to run

```bash
# Full suite from repo root
python3 -m unittest discover installer/tests/

# Single module, verbose
python3 -m unittest installer.tests.test_class1_chain_verify -v

# Single integration pass with a custom fixture
TEST_EFI_BINARY=/path/to/some.efi \
  python3 -m unittest installer.tests.test_class1_integration -v
```

## Harness classes

Forge SB coverage is organized into classes. Shipped classes are the ones with
concrete files in this directory; the others are the planned shape.

| Class | Question answered | Status |
|-------|-------------------|--------|
| 1 | Does our signing chain (MOK -> grub -> kernel) verify end-to-end against the MOK cert? | **Shipped** (`class1_chain_verify.py` + two test modules) |
| 2 | Post-reboot: is Secure Boot actually enabled + PK-enrolled (not Setup Mode)? | **Shipped** (`class2_runtime_sb_state.py` + test module) |
| 2b | Did the installer register an InterGenOS entry in the UEFI boot order? | **Shipped** (`class2b_boot_order.py` + test module) |
| 3 | Shim-specific (signed by Fedora/MS, not us) | Deferred |
| 4 | (reserved) | Deferred |
| 5 | Kernel module signatures (`MODULE_SIG`, not PE/COFF) | **Shipped** (`class5_module_sigs.py` + test module) |
| 6 | (reserved) | Deferred |
| 7 | (reserved) | Deferred |

### Class 1 — signed-chain verification

* `class1_chain_verify.py` — the verifier itself. Walks `<target>/boot/efi/...`
  and `<target>/boot/vmlinuz-*`, runs `sbverify` against the MOK cert, emits
  a JSON report. Skips the shim stage cleanly (Fedora/MS-signed, not ours).
* `test_class1_chain_verify.py` — 27 unit tests; mocks `subprocess.run` so the
  parsing/reporting logic is exercised on any host.
* `test_class1_integration.py` — 4 integration tests; real `openssl` +
  `sbsign` + `sbverify` against a staged target root. Skip-gates on the tools
  being installed and on the `TEST_EFI_BINARY` fixture being readable.

### Class 2 — runtime Secure Boot state

Asserts the running system is in the locked-down posture the signing chain
claimed. Two required probes + one supplementary:

* **SecureBoot** EFI variable must equal `1` (SB enforcement on).
* **SetupMode** EFI variable must equal `0` (PK enrolled; firmware not
  accepting new platform keys). `SetupMode=1` means SB is nominally on but
  the firmware is unlocked, so it would silently accept a new PK — which
  defeats tamper resistance.
* `mokutil --sb-state` — supplementary user-space cross-check; may disagree
  with the EFI variable if the shim/MOK database has desynced from the
  firmware. Skips cleanly when `mokutil` isn't installed.

Files: `class2_runtime_sb_state.py` (module with CLI) +
`test_class2_runtime_sb_state.py` (19 unit tests; mocks efivars via temp
dir + `ATTR_HEADER` + 1 byte payload, mocks `mokutil` via `subprocess.run`).

Reading a real efivars variable normally requires root. The CLI returns a
clean "permission denied" result rather than raising, so a non-root dev
can still run shape-tests. Point at a mock efivars tree via:

```bash
python3 -m installer.tests.class2_runtime_sb_state --efivars-dir /tmp/mock
```

### Class 2b — UEFI boot-order verification

Complements Class 2: where Class 2 proves SB is *enforced at runtime*,
Class 2b proves the installer *left behind a discoverable InterGenOS
entry* — so firmware boots it automatically rather than relying on the
`/EFI/BOOT/bootx64.efi` removable-media fallback path. Catches the
silent failure mode where `efibootmgr --create` in the installer soft-
failed but the install otherwise appeared successful (see
`installer/backend/bootloader.py:189` TODO).

Three probes:

* **entry-exists** (required): an `InterGenOS`-labeled `Boot####` entry
  is present in the firmware variable store. Multiple matches pass with
  a "duplicate entries" detail note (soft warning, not a fail — a
  stricter policy could be opted into later).
* **entry-in-boot-order** (required): the entry's `Boot####` ID appears
  in the `BootOrder` list. Presence alone isn't enough — firmware only
  iterates entries listed in `BootOrder` during normal boot.
* **boot-current** (supplementary): if `BootCurrent` points at the
  InterGenOS entry, we actually booted from it. Only meaningful when
  the probe runs on an installed InterGenOS target; on a build host
  it'll typically fail because the host booted from its own entry. The
  supplementary designation means it does NOT mark the overall report
  FAIL.

Data source: `efibootmgr -v` stdout (parsed via compiled regexes, IDs
uppercased for comparison). We don't parse raw `EFI_LOAD_OPTION` binary
— efibootmgr already does that for us correctly per UEFI spec.

Files: `class2b_boot_order.py` (module + CLI) +
`test_class2b_boot_order.py` (23 unit tests; mocks `shutil.which` and
`subprocess.run` so no real efibootmgr / efivars / root needed).

Label override via `--label` for forks / rebranded installs.

### Class 5 — kernel module signature enforcement

Post-boot probe that the running kernel enforces module signatures +
carries a usable trust anchor. Distinct from Class 1 (PE/COFF chain):
module signatures are PKCS#7-formatted `MODULE_SIG` blocks appended to
`.ko` files, verified by the kernel against its keyring rather than via
`sbverify`.

Three probes, all checked via `/proc`:

* **module_sig_enforce** (required): `/proc/sys/kernel/module_sig_enforce`
  reads as `"1"`. Absent file means `CONFIG_MODULE_SIG_FORCE=n` — the
  kernel will load unsigned modules. On the integration tier this is
  the first probe to fail on stock Ubuntu / Fedora builds that don't
  compile with the force flag.
* **signing_keyring** (required): `/proc/keys` contains at least one of
  `.secondary_trusted_keys`, `.machine`, or `.platform`. Any one is
  sufficient; `.machine` holds MOK-enrolled keys on shim-aware kernels.
* **sampled_module_signed** (required when modules loaded; skip-clean
  when empty): the first module from `/proc/modules` has a PKCS#7
  signature visible in `modinfo` output (fields `signer:`, `sig_id:`,
  `sig_hashalgo:` all present). A monolithic kernel (`/proc/modules`
  empty) is the most-secure posture and passes cleanly with a note.

Override the sampled module via `--sample-module NAME` (useful for
pinning a module known to be signed/unsigned during targeted checks).

Files: `class5_module_sigs.py` (module + CLI) +
`test_class5_module_sigs.py` (26 unit tests; mocks `shutil.which` and
`subprocess.run` for modinfo, temp-dir proc paths for everything else).

### Phase A — GRUB `check_signatures=enforce` empirical

In flight. Answers: does `enforce` refuse our PE/COFF sbsigned kernel (main's
2026-04-20 hypothesis)?

* `secboot_vm_profile.py` — throwaway libvirt/QEMU VM helper (OVMF
  secboot + MS VARS + swtpm TPM2 + SMM + q35). CLI: `--check-only`, `--destroy`,
  `--disk`, `--iso`, `--json`. Skip-gates on libvirtd + virt-install + swtpm +
  OVMF firmware files.
* `test_grub_check_signatures.py` — 4 host-level tests (grub-mkimage present,
  `check_signatures=enforce` parses, `pgp.mod` + `signature_test.mod` present)
  plus 3 VM-tier tests (plumbing + hypothesis + null skeletons).

Phase A-2 plumbing (grub.cfg write + serial capture + log parser) lands once
libvirtd is running on the build host and an installed InterGenOS disk image
exists to boot.

### Other modules present

* `test_mok.py` — 16 tests for `mok.py` common-name regex + password ASCII
  guards (M1 + M2 from the 2026-04-20 hardening bundle).

## Skip-gate conventions

Tests use `@unittest.skipUnless(...)` at the class level when they depend on a
host tool being installed. The pattern surfaces *why* a test skipped rather
than silently passing — run with `-v` to see the skip reason.

| Dependency | Tests that require it | Skip reason when absent |
|------------|----------------------|-------------------------|
| `sbsign`, `sbverify`, `openssl` | Class 1 integration | "integration prerequisites missing: <tool> not in PATH" |
| `/usr/share/refind/refind/drivers_x64/ext4_x64.efi` (or `TEST_EFI_BINARY`) | Class 1 integration | "test EFI binary not found at ..." |
| `grub-mkimage`, `grub-script-check` | GRUB build sanity | "grub-mkimage / grub-script-check not in PATH" |
| `libvirtd` active + `virt-install` + `swtpm` + OVMF secboot code + MS VARS | Phase A VM tier | "VM prerequisites missing: ..." (enumerated) |
| Disk image at `TEST_DISK_IMAGE` env var (default `/var/lib/libvirt/images/intergenos-target.qcow2`) | Phase A hypothesis/null skeletons | "disk image ... not found (Phase A-2)" |

## Expected counts by host

These numbers change over time; treat as a current-state snapshot rather than
a contract.

| Host | Tests | Passed | Skipped | Notes |
|------|-------|--------|---------|-------|
| Typical dev laptop (no `sbsign`, no libvirtd) | 129 | 115 | 14 | Class 1 integration + post-install + Phase A VM tier skip + all 6 post-install-integration tests skip (not Forge-installed); Classes 2 / 2b / 5 unit tests fully mocked, run anywhere |
| ubuntu2404 build host (`sbsign` + OVMF available, libvirtd may be inactive) | 129 | 119 | 10 | Phase A VM tier skips when libvirtd stopped; post-install tier skips absent a Forge-installed runtime (ubuntu2404 isn't InterGenOS) |
| Inside a Forge-installed InterGenOS target (`/var/lib/intergen/mok/mok.crt` present, target=`/`) | 129 | up to 126 | 3+ | All four post-install integration classes activate and exercise real state |

### Post-install integration tier

Every unit-mocked class has a sibling integration class that runs
against the *real* installed system rather than mocks. Unit tests prove
"our probe logic works"; integration tests prove "this actual install
is in the posture we claim." Both should be green — the split is about
*what* is being asserted, not *whether* the assertion is true.

Four integration classes:

* **`TestClass1Integration`** — "does our signing *logic* work when
  invoked?" Stages a target, signs it with a test MOK, walks the chain.
  Runs wherever `sbsign` / `sbverify` / `openssl` are installed.
  (Shipped alongside Class 1 unit tests in `test_class1_integration.py`.)
* **`TestClass1PostInstall`** — "did the actual install produce a
  correctly-signed target?" Walks a real installed filesystem. Skip-
  gates on `<target>/var/lib/intergen/mok/mok.crt` existing (the
  authoritative "this is a Forge install" signal). Point at a mounted
  target via `CLASS1_POST_INSTALL_TARGET=/path/to/mount/point`. (Also
  in `test_class1_integration.py`.)
* **`TestClass2PostReboot`** — exercises `class2_runtime_sb_state.run()`
  against live `/sys/firmware/efi/efivars`. Catches "SB was disabled
  between install and boot" / "SetupMode leaked back to 1." In
  `test_post_install_integration.py`.
* **`TestClass2bPostReboot`** — exercises `class2b_boot_order.run()`
  against live `efibootmgr`. Catches "entry missing" / "entry exists
  but not in BootOrder" / "OEM rewrote BootOrder at POST." In
  `test_post_install_integration.py`.
* **`TestClass5PostInstall`** — exercises `class5_module_sigs.run()`
  against live `/proc`. Catches kernel-without-SIG_FORCE + missing
  trust-anchor keyring + unsigned loaded modules. In
  `test_post_install_integration.py`.

Classes 2 / 2b / 5 runtime-state probes can only answer their question
on the live target (they read `/sys` and `/proc`, not a mounted disk).
`POST_INSTALL_TARGET != /` is intentionally rejected with a clear skip
reason — there's nothing meaningful to assert against a mounted-but-
not-booted image.

## Upcoming

* **Phase A-2 plumbing for `test_grub_check_signatures`** — see the
  Phase A section above. Ships when libvirtd is active on ubuntu2404
  + an installed InterGenOS disk image exists (scheduled
  Monday/Tuesday install window). Once both land, the skeletons at
  `test_enforce_refuses_pecoff_kernel` / `test_noenforce_boots_pecoff_kernel`
  become runnable — grub.cfg write path + serial console capture +
  log parser for "file has no signature" vs successful kernel handoff.
* **Class 3 (shim)** remains Deferred unless a specific InterGenOS-
  controlled property turns up worth asserting. Shim is signed by
  Fedora / MS, not us — there isn't currently a question Class 3 would
  answer that the existing shipped classes don't already cover.

See each test module's top docstring for design rationale and the specific
failure modes it catches.
