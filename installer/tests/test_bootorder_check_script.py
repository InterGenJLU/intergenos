# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""intergenos-bootorder-check — the shipped boot-order checker.

The installer registers a UEFI boot entry and `efibootmgr --create` puts it
first. Measured on a UEFI 2.110 AMI board (2026-07-31): the installed system
came up with `BootOrder: 0001,0000` where 0001 was the firmware's own
"UEFI OS" entry for \\EFI\\BOOT\\BOOTX64.EFI on the same ESP, so the machine
booted the fallback loader instead of the registered entry.

These tests drive the shipped script against a stub efibootmgr — no firmware,
no root, no prompts — and pin its whole decision matrix, including the two
cases where it must NOT touch NVRAM (the user kept another OS as the default;
no recorded intent) and the case where the firmware refuses the write.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1]
          / "bootorder" / "bootorder-check.sh")

_NVRAM_DEMOTED = """BootCurrent: 0001
Timeout: 1 seconds
BootOrder: 0001,0000
Boot0000* InterGenOS\tHD(1,GPT,aaaa,0x800,0x200000)/\\EFI\\InterGenOS\\shimx64.efi
Boot0001* UEFI OS\tHD(1,GPT,aaaa,0x800,0x200000)/\\EFI\\BOOT\\BOOTX64.EFI0000424f
"""

_NVRAM_CORRECT = """BootCurrent: 0000
BootOrder: 0000,0001
Boot0000* InterGenOS\tHD(1,GPT,aaaa,0x800,0x200000)/\\EFI\\InterGenOS\\shimx64.efi
Boot0001* UEFI OS\tHD(1,GPT,aaaa,0x800,0x200000)/\\EFI\\BOOT\\BOOTX64.EFI0000424f
"""

_NVRAM_NO_ENTRY = """BootCurrent: 0001
BootOrder: 0001
Boot0001* UEFI OS\tHD(1,GPT,aaaa,0x800,0x200000)/\\EFI\\BOOT\\BOOTX64.EFI0000424f
"""

# Our entry exists but is absent from BootOrder entirely — the firmware never
# considers it during normal boot.
_NVRAM_OUT_OF_ORDER = """BootCurrent: 0001
BootOrder: 0001,0002
Boot0000* InterGenOS\tHD(1,GPT,aaaa,0x800,0x200000)/\\EFI\\InterGenOS\\shimx64.efi
Boot0001* UEFI OS\tHD(1,GPT,aaaa,0x800,0x200000)/\\EFI\\BOOT\\BOOTX64.EFI0000424f
Boot0002* Windows Boot Manager\tHD(1,GPT,bbbb,0x800,0x32000)/\\EFI\\Microsoft\\Boot\\bootmgfw.efi
"""

# A label that merely STARTS WITH ours must not be mistaken for ours.
_NVRAM_SIMILAR_LABEL = """BootCurrent: 0001
BootOrder: 0001,0000
Boot0000* InterGenOS Recovery\tHD(1,GPT,cccc,0x800,0x1000)/\\EFI\\rec\\shimx64.efi
Boot0001* UEFI OS\tHD(1,GPT,aaaa,0x800,0x200000)/\\EFI\\BOOT\\BOOTX64.EFI
"""


class _ScriptHarness(unittest.TestCase):
    """Runs the script with a stub efibootmgr whose behavior each test picks.

    The stub records every invocation to calls.log and, when `-o ORDER` is
    passed, rewrites the NVRAM fixture so the script's read-back sees the
    result — the same read-back a real firmware would answer.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.nvram = self.dir / "nvram.txt"
        self.calls = self.dir / "calls.log"
        self.efi_dir = self.dir / "sys-firmware-efi"
        self.efi_dir.mkdir()
        self.stub = self.dir / "efibootmgr-stub"
        self.intent = self.dir / "boot-default.conf"

    def _write_stub(self, write_rc=0, honor_write=True, list_rc=0):
        self.stub.write_text(textwrap.dedent(f"""\
            #!/bin/bash
            echo "$@" >> "{self.calls}"
            if [ "${{1:-}}" = "-o" ]; then
                if [ {write_rc} -ne 0 ]; then
                    echo "could not set BootOrder: Operation not permitted" >&2
                    exit {write_rc}
                fi
                if [ {1 if honor_write else 0} -eq 1 ]; then
                    sed -i "s/^BootOrder: .*/BootOrder: $2/" "{self.nvram}"
                fi
                exit 0
            fi
            if [ {list_rc} -ne 0 ]; then
                echo "EFI variables are not supported on this system." >&2
                exit {list_rc}
            fi
            cat "{self.nvram}"
            """))
        self.stub.chmod(0o755)

    def _write_intent(self, value):
        self.intent.write_text(
            "# test fixture\n"
            f"default_boot_target={value}\n"
            "boot_entry_label=InterGenOS\n"
        )

    def _run(self, extra=()):
        cmd = [
            "/bin/bash", str(SCRIPT),
            "--intent-file", str(self.intent),
            "--efibootmgr", str(self.stub),
            "--efi-dir", str(self.efi_dir),
            *extra,
        ]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    def _order_writes(self):
        if not self.calls.exists():
            return []
        return [l for l in self.calls.read_text().splitlines()
                if l.startswith("-o ")]


class RepairTests(_ScriptHarness):
    def test_firmware_demotion_is_repaired_and_verified(self):
        self.nvram.write_text(_NVRAM_DEMOTED)
        self._write_stub()
        self._write_intent("yes")
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self._order_writes(), ["-o 0000,0001"])
        self.assertIn("repaired", res.stdout)
        self.assertIn("BootOrder: 0000,0001", self.nvram.read_text())

    def test_entry_absent_from_bootorder_is_added_first(self):
        self.nvram.write_text(_NVRAM_OUT_OF_ORDER)
        self._write_stub()
        self._write_intent("yes")
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        # Our entry leads; every other entry keeps its relative order.
        self.assertEqual(self._order_writes(), ["-o 0000,0001,0002"])

    def test_already_first_writes_nothing(self):
        self.nvram.write_text(_NVRAM_CORRECT)
        self._write_stub()
        self._write_intent("yes")
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self._order_writes(), [])
        self.assertIn("no change", res.stdout)

    def test_dry_run_reports_without_writing(self):
        self.nvram.write_text(_NVRAM_DEMOTED)
        self._write_stub()
        self._write_intent("yes")
        res = self._run(extra=["--dry-run"])
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self._order_writes(), [])
        self.assertIn("dry run", res.stdout)


class UserControlTests(_ScriptHarness):
    def test_declined_default_is_never_overridden(self):
        self.nvram.write_text(_NVRAM_DEMOTED)
        self._write_stub()
        self._write_intent("no")
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self._order_writes(), [],
                         "the user kept another OS as the default; the "
                         "checker must not take the machine over")
        self.assertIn("reporting only", res.stdout)

    def test_no_recorded_intent_reports_only(self):
        self.nvram.write_text(_NVRAM_DEMOTED)
        self._write_stub()
        # No intent file written at all.
        res = self._run()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self._order_writes(), [])
        self.assertIn("no recorded install intent", res.stdout)


class UndeterminedStateTests(_ScriptHarness):
    """A check that cannot check reports that — never a pass, never a fail."""

    def test_non_efi_system_exits_zero_without_reading_nvram(self):
        self.nvram.write_text(_NVRAM_DEMOTED)
        self._write_stub()
        self._write_intent("yes")
        os.rmdir(self.efi_dir)
        res = self._run()
        self.assertEqual(res.returncode, 0)
        self.assertIn("not an EFI system", res.stdout)
        self.assertEqual(self._order_writes(), [])

    def test_efibootmgr_missing_exits_zero(self):
        self.nvram.write_text(_NVRAM_DEMOTED)
        self._write_intent("yes")
        # stub never created
        res = self._run()
        self.assertEqual(res.returncode, 0)
        self.assertIn("cannot determine", res.stdout)

    def test_efibootmgr_read_failure_exits_zero(self):
        self.nvram.write_text(_NVRAM_DEMOTED)
        self._write_stub(list_rc=2)
        self._write_intent("yes")
        res = self._run()
        self.assertEqual(res.returncode, 0)
        self.assertIn("cannot determine", res.stdout)

    def test_missing_entry_is_reported_not_repaired(self):
        self.nvram.write_text(_NVRAM_NO_ENTRY)
        self._write_stub()
        self._write_intent("yes")
        res = self._run()
        self.assertEqual(res.returncode, 0)
        self.assertEqual(self._order_writes(), [])
        self.assertIn("no boot entry labelled", res.stdout)

    def test_similar_label_is_not_treated_as_ours(self):
        self.nvram.write_text(_NVRAM_SIMILAR_LABEL)
        self._write_stub()
        self._write_intent("yes")
        res = self._run()
        self.assertEqual(res.returncode, 0)
        self.assertEqual(self._order_writes(), [],
                         "'InterGenOS Recovery' is a different entry")
        self.assertIn("no boot entry labelled", res.stdout)


class WriteFailureTests(_ScriptHarness):
    def test_rejected_write_fails_loudly(self):
        self.nvram.write_text(_NVRAM_DEMOTED)
        self._write_stub(write_rc=1)
        self._write_intent("yes")
        res = self._run()
        self.assertEqual(res.returncode, 1)
        self.assertIn("writing BootOrder failed", res.stdout)

    def test_write_accepted_but_ignored_by_firmware_fails_loudly(self):
        self.nvram.write_text(_NVRAM_DEMOTED)
        self._write_stub(honor_write=False)
        self._write_intent("yes")
        res = self._run()
        self.assertEqual(res.returncode, 1,
                         "a firmware that takes the write and keeps its own "
                         "order must not read as a repair")
        self.assertIn("still not first", res.stdout)


if __name__ == "__main__":
    unittest.main()
