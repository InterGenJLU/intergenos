# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Truth controls for boot and signing smoke checks."""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_DIR = REPO_ROOT / "installer" / "smoke"
LIB_SH = SMOKE_DIR / "lib.sh"
BOOT_SH = SMOKE_DIR / "checks" / "boot.sh"
SIGNING_SH = SMOKE_DIR / "checks" / "signing.sh"


class ShellCheckCase(unittest.TestCase):
    def run_check(
        self,
        module: Path,
        function: str,
        *,
        env_update: dict[str, str] | None = None,
        stubs: dict[str, str] | None = None,
        shell_setup: str = "",
    ) -> list[tuple[str, str, str]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for name, body in (stubs or {}).items():
                stub = bin_dir / name
                stub.write_text("#!/usr/bin/bash\n" + textwrap.dedent(body))
                stub.chmod(0o755)

            script = textwrap.dedent(
                f"""
                set -uo pipefail
                SMOKE_JSON=1
                SMOKE_VERBOSE=0
                SMOKE_STRICT=0
                . "{LIB_SH}"
                . "{module}"
                {shell_setup}
                {function}
                for sol013_row in "${{SMOKE_RESULTS[@]}}"; do
                    printf '%s\\n' "$sol013_row"
                done
                """
            )
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
            env.update(env_update or {})
            result = subprocess.run(
                ["/usr/bin/bash", "-c", script],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = []
            for line in result.stdout.splitlines():
                if line.count("|") >= 2:
                    rows.append(tuple(line.split("|", 2)))
            return rows

    def one(self, rows: list[tuple[str, str, str]], check_id: str):
        matches = [row for row in rows if row[1] == check_id]
        self.assertEqual(len(matches), 1, rows)
        return matches[0]


class BootTruthTests(ShellCheckCase):
    def test_dmesg_probe_failure_never_passes_as_clean(self):
        rows = self.run_check(
            BOOT_SH,
            "check_boot_dmesg_clean",
            stubs={"dmesg": "printf '%s\\n' 'read denied' >&2\nexit 5\n"},
        )
        status, _, message = self.one(rows, "boot/dmesg")
        self.assertEqual(status, "WARN")
        self.assertIn("could not read dmesg", message)

    def test_uefi_without_efivars_is_warning_not_bios_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            firmware = Path(tmp) / "sys/firmware/efi"
            firmware.mkdir(parents=True)
            rows = self.run_check(
                BOOT_SH,
                "check_boot_secureboot_state",
                env_update={"SMOKE_EFI_FIRMWARE": str(firmware)},
                stubs={
                    "mokutil": "printf '%s\\n' 'EFI variables are not supported on this system'\n"
                },
            )
        status, _, _ = self.one(rows, "boot/sb-state")
        self.assertEqual(status, "WARN")

    def test_uefi_missing_esp_is_failure_not_bios_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            firmware = root / "sys/firmware/efi"
            firmware.mkdir(parents=True)
            cmdline = root / "cmdline"
            cmdline.write_text("root=/dev/mapper/cryptroot rw\n")
            rows = self.run_check(
                BOOT_SH,
                "check_boot_efi_artifacts",
                env_update={
                    "SMOKE_CMDLINE": str(cmdline),
                    "SMOKE_EFI_FIRMWARE": str(firmware),
                    "SMOKE_ESP_ROOT": str(root / "boot/efi"),
                    "SMOKE_BOOT_EFI_DIR": str(root / "boot/efi/EFI"),
                },
            )
        self.assertEqual(self.one(rows, "boot/efi-artifacts")[0], "FAIL")

    def test_unreadable_esp_is_warning_with_root_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            firmware = root / "sys/firmware/efi"
            firmware.mkdir(parents=True)
            cmdline = root / "cmdline"
            cmdline.write_text("root=/dev/mapper/cryptroot rw\n")
            rows = self.run_check(
                BOOT_SH,
                "check_boot_efi_artifacts",
                env_update={
                    "SMOKE_CMDLINE": str(cmdline),
                    "SMOKE_EFI_FIRMWARE": str(firmware),
                    "SMOKE_ESP_ROOT": str(root / "boot/efi"),
                    "SMOKE_BOOT_EFI_DIR": str(root / "boot/efi/EFI"),
                },
                shell_setup="smoke_path_state() { printf unreadable; }",
            )
        status, _, message = self.one(rows, "boot/efi-artifacts")
        self.assertEqual(status, "WARN")
        self.assertIn("re-run as root:", message)

    def test_complete_efi_pair_passes_and_partial_pair_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            firmware = root / "sys/firmware/efi"
            efi_dir = root / "boot/efi/EFI/InterGenOS"
            firmware.mkdir(parents=True)
            efi_dir.mkdir(parents=True)
            (efi_dir / "shimx64.efi").write_bytes(b"shim")
            (efi_dir / "grubx64.efi").write_bytes(b"grub")
            cmdline = root / "cmdline"
            cmdline.write_text("root=/dev/mapper/cryptroot rw\n")
            common = {
                "SMOKE_CMDLINE": str(cmdline),
                "SMOKE_EFI_FIRMWARE": str(firmware),
                "SMOKE_ESP_ROOT": str(root / "boot/efi"),
                "SMOKE_BOOT_EFI_DIR": str(root / "boot/efi/EFI"),
            }
            rows = self.run_check(BOOT_SH, "check_boot_efi_artifacts", env_update=common)
            self.assertEqual(self.one(rows, "boot/efi-artifacts")[0], "PASS")
            (efi_dir / "shimx64.efi").unlink()
            rows = self.run_check(BOOT_SH, "check_boot_efi_artifacts", env_update=common)
        self.assertEqual(self.one(rows, "boot/efi-artifacts")[0], "FAIL")

    def test_initramfs_stub_alone_cannot_witness_a_kernel(self):
        with tempfile.TemporaryDirectory() as tmp:
            boot = Path(tmp) / "boot"
            boot.mkdir()
            (boot / "initramfs-plain-stub.img").write_bytes(b"x" * 50)
            rows = self.run_check(
                BOOT_SH,
                "check_boot_kernel_present",
                env_update={"SMOKE_BOOT_DIR": str(boot)},
            )
        self.assertEqual(self.one(rows, "boot/kernel")[0], "FAIL")

    def test_kernel_plus_small_stub_passes_without_counting_stub_as_kernel(self):
        with tempfile.TemporaryDirectory() as tmp:
            boot = Path(tmp) / "boot"
            boot.mkdir()
            (boot / "vmlinuz-test").write_bytes(b"kernel")
            (boot / "initramfs-plain-stub.img").write_bytes(b"x" * 50)
            rows = self.run_check(
                BOOT_SH,
                "check_boot_kernel_present",
                env_update={"SMOKE_BOOT_DIR": str(boot)},
            )
        status, _, message = self.one(rows, "boot/kernel")
        self.assertEqual(status, "PASS")
        self.assertIn("1 kernel", message)
        self.assertIn("stub", message)


class SigningTruthTests(ShellCheckCase):
    def test_unreadable_mok_is_not_reported_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mok_dir = root / "mok"
            mok_dir.mkdir()
            cert = mok_dir / "mok.crt"
            rows = self.run_check(
                SIGNING_SH,
                "check_signing_mok_enrolled",
                env_update={
                    "SMOKE_MOK_DIR": str(mok_dir),
                    "SMOKE_MOK_CERT": str(cert),
                    "SMOKE_SB_STATE_OVERRIDE": "enabled",
                },
                stubs={"mokutil": "exit 0\n"},
                shell_setup="smoke_path_state() { printf unreadable; }",
            )
        status, _, message = self.one(rows, "sign/mok-enrolled")
        self.assertEqual(status, "WARN")
        self.assertIn("unreadable", message)
        self.assertIn("re-run as root:", message)

    def test_truly_absent_mok_fails_under_secure_boot(self):
        with tempfile.TemporaryDirectory() as tmp:
            mok_dir = Path(tmp) / "mok"
            mok_dir.mkdir()
            rows = self.run_check(
                SIGNING_SH,
                "check_signing_mok_enrolled",
                env_update={
                    "SMOKE_MOK_DIR": str(mok_dir),
                    "SMOKE_MOK_CERT": str(mok_dir / "mok.crt"),
                    "SMOKE_SB_STATE_OVERRIDE": "enabled",
                },
                stubs={"mokutil": "exit 0\n"},
            )
        self.assertEqual(self.one(rows, "sign/mok-enrolled")[0], "FAIL")

    def test_truly_absent_mok_skips_when_secure_boot_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            mok_dir = Path(tmp) / "mok"
            mok_dir.mkdir()
            rows = self.run_check(
                SIGNING_SH,
                "check_signing_mok_enrolled",
                env_update={
                    "SMOKE_MOK_DIR": str(mok_dir),
                    "SMOKE_MOK_CERT": str(mok_dir / "mok.crt"),
                    "SMOKE_SB_STATE_OVERRIDE": "disabled",
                },
                stubs={"mokutil": "exit 0\n"},
            )
        self.assertEqual(self.one(rows, "sign/mok-enrolled")[0], "SKIP")

    def test_missing_secondary_keyring_is_indeterminate_for_nonroot(self):
        rows = self.run_check(
            SIGNING_SH,
            "check_signing_secondary_keyring",
            env_update={"SMOKE_EUID": "1000"},
            stubs={
                "keyctl": "printf \"Can't find 'keyring:.secondary_trusted_keys'\\n\" >&2\nexit 1\n"
            },
        )
        status, _, message = self.one(rows, "sign/secondary-keyring")
        self.assertEqual(status, "WARN")
        self.assertIn("re-run as root:", message)

    def test_chain_root_requires_both_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            firmware = root / "sys/firmware/efi"
            esp = root / "boot/efi"
            firmware.mkdir(parents=True)
            esp.mkdir(parents=True)
            grub = esp / "grubx64.efi"
            grub.write_bytes(b"grub")
            rows = self.run_check(
                SIGNING_SH,
                "check_signing_chain_root",
                env_update={
                    "SMOKE_EFI_FIRMWARE": str(firmware),
                    "SMOKE_ESP_ROOT": str(esp),
                    "SMOKE_SHIM_EFI": str(esp / "shimx64.efi"),
                    "SMOKE_GRUB_EFI": str(grub),
                },
                stubs={"sbverify": "printf '%s\\n' 'image signature issuer: /CN=fixture/'\n"},
            )
        self.assertEqual(self.one(rows, "sign/chain-root")[0], "FAIL")

    def test_chain_root_complete_pair_reports_only_what_was_proven(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            firmware = root / "sys/firmware/efi"
            esp = root / "boot/efi"
            firmware.mkdir(parents=True)
            esp.mkdir(parents=True)
            shim = esp / "shimx64.efi"
            grub = esp / "grubx64.efi"
            shim.write_bytes(b"shim")
            grub.write_bytes(b"grub")
            rows = self.run_check(
                SIGNING_SH,
                "check_signing_chain_root",
                env_update={
                    "SMOKE_EFI_FIRMWARE": str(firmware),
                    "SMOKE_ESP_ROOT": str(esp),
                    "SMOKE_SHIM_EFI": str(shim),
                    "SMOKE_GRUB_EFI": str(grub),
                },
                stubs={"sbverify": "printf '%s\\n' 'image signature issuer: /CN=fixture/'\n"},
            )
        status, _, message = self.one(rows, "sign/chain-root")
        self.assertEqual(status, "PASS")
        self.assertIn("PE signature records", message)
        self.assertIn("trust roots not validated here", message)

    def test_chain_root_unreadable_is_not_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            firmware = root / "sys/firmware/efi"
            firmware.mkdir(parents=True)
            rows = self.run_check(
                SIGNING_SH,
                "check_signing_chain_root",
                env_update={
                    "SMOKE_EFI_FIRMWARE": str(firmware),
                    "SMOKE_ESP_ROOT": str(root / "boot/efi"),
                    "SMOKE_SHIM_EFI": str(root / "boot/efi/shimx64.efi"),
                    "SMOKE_GRUB_EFI": str(root / "boot/efi/grubx64.efi"),
                },
                stubs={"sbverify": "exit 0\n"},
                shell_setup="smoke_path_state() { printf unreadable; }",
            )
        status, _, message = self.one(rows, "sign/chain-root")
        self.assertEqual(status, "WARN")
        self.assertIn("re-run as root:", message)


if __name__ == "__main__":
    unittest.main()
