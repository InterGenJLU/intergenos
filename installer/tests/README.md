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
| 2 | Post-reboot: is Secure Boot actually enabled + PK-enrolled (not Setup Mode)? | **Shipped** (`class2_runtime_sb_state.py` + test module); boot-order sub-probe still planned |
| 3 | Shim-specific (signed by Fedora/MS, not us) | Deferred |
| 4 | (reserved) | Deferred |
| 5 | Kernel module signatures (`MODULE_SIG`, not PE/COFF) | Planned |
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
| Typical dev laptop (no `sbsign`, no libvirtd) | 74 | 66 | 8 | Class 1 integration + post-install + Phase A VM tier skip; Class 2 unit tests fully mocked, run anywhere |
| ubuntu2404 build host (`sbsign` + OVMF available, libvirtd may be inactive) | 74 | 70 | 4 | Phase A VM tier skips when libvirtd stopped; post-install skips absent a real installed target |
| Inside a Forge-installed InterGenOS target (`/var/lib/intergen/mok/mok.crt` present) | 74 | up to 71 | 3+ | `TestClass1PostInstall` activates and walks the real target |

### Class 1 post-install verification

Ships alongside Class 1 integration. The design question both answer:

* `TestClass1Integration` — "does our signing *logic* work when invoked?" Stages a target, signs it with a test MOK, walks the chain.
* `TestClass1PostInstall` — "did the actual install produce a correctly-signed target?" Walks a real installed filesystem. Skip-gates on `<target>/var/lib/intergen/mok/mok.crt` existing, which is the authoritative "this is a Forge install" signal.

Point at a different target via `CLASS1_POST_INSTALL_TARGET=/path/to/mount/point`.

## Upcoming

* **Class 2b — UEFI boot-order verification** (originally bundled with
  Class 2, now split). Consumes `installer/backend/bootloader.py:189`
  TODO: "did the installer actually register an InterGenOS entry in the
  UEFI boot order?" Implementation probes via `efibootmgr` output parsing
  + cross-check against `Boot####` EFI variables.
* **Class 5** — kernel module signatures (`MODULE_SIG`, not the PE/COFF path
  Class 1 walks). Deferred until Class 2b ships.

See each test module's top docstring for design rationale and the specific
failure modes it catches.
