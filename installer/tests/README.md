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
| 2 | Post-reboot: is Secure Boot actually enabled, and did our UEFI BOOT entry register? | Planned (consumes `bootloader.py:189` TODO) |
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
| Typical dev laptop (no `sbsign`, no libvirtd) | 54 | 47 | 7 | Class 1 integration + Phase A VM tier skip |
| ubuntu2404 build host (`sbsign` + OVMF available, libvirtd may be inactive) | 54 | 51 | 3 | Only Phase A VM tier skips when libvirtd is stopped |
| ubuntu2404 with libvirtd active + disk image staged | 54 | 52-54 | 0-2 | Depends on Phase A-2 plumbing state |

## Upcoming

* **Class 1 kernel stage re-gate to post-install** (2026-04-20 owner/main poll:
  Option 2). The packaged kernel is signed at install time with the user's MOK,
  not at package build time. The current Class 1 integration test exercises
  "does our signing *work* when we do it" correctly; a new `TestClass1PostInstall`
  class will exercise "did the install produce a correctly-signed target."
* **Class 2** — post-reboot UEFI boot-order + runtime SB-state verification.
  Consumes `installer/backend/bootloader.py:189` TODO ("did UEFI boot order
  include InterGenOS entry post-reboot?"). Host-testable via `/sys/firmware/efi/efivars`
  + `mokutil --sb-state`.
* **Class 5** — kernel module signatures (`MODULE_SIG`, not the PE/COFF path
  Class 1 walks). Deferred until Class 2 ships.

See each test module's top docstring for design rationale and the specific
failure modes it catches.
