# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Truth controls for hardware venue and unclaimed-device smoke checks."""

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
HARDWARE_SH = SMOKE_DIR / "checks" / "hardware.sh"


class HardwareTruthTests(unittest.TestCase):
    def run_check(self, root: Path, function: str):
        bin_dir = root / "_stubs"
        bin_dir.mkdir(parents=True, exist_ok=True)
        for name in ("lspci", "aplay"):
            stub = bin_dir / name
            stub.write_text("#!/usr/bin/bash\nexit 0\n")
            stub.chmod(0o755)
        script = textwrap.dedent(
            f"""
            set -uo pipefail
            SMOKE_JSON=1
            SMOKE_HW_ROOT="{root}"
            SMOKE_HW_LSPCI="{bin_dir / 'lspci'}"
            SMOKE_HW_APLAY="{bin_dir / 'aplay'}"
            SMOKE_HW_FORCE_VIRT=0
            . "{LIB_SH}"
            . "{HARDWARE_SH}"
            {function}
            for sol013_row in "${{SMOKE_RESULTS[@]}}"; do
                printf '%s\\n' "$sol013_row"
            done
            """
        )
        result = subprocess.run(
            ["/usr/bin/bash", "-c", script],
            capture_output=True,
            text=True,
            env=dict(os.environ),
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return [tuple(line.split("|", 2)) for line in result.stdout.splitlines() if line.count("|") >= 2]

    def one(self, rows, check_id):
        matches = [row for row in rows if row[1] == check_id]
        self.assertEqual(len(matches), 1, rows)
        return matches[0]

    @staticmethod
    def make_pci(root: Path, address: str, class_code: str, *, bound: bool = False):
        device = root / "sys/bus/pci/devices" / address
        device.mkdir(parents=True)
        (device / "class").write_text(class_code + "\n")
        if bound:
            (device / "driver").mkdir()

    @staticmethod
    def write_inputs(root: Path, text: str):
        inputs = root / "proc/bus/input/devices"
        inputs.parent.mkdir(parents=True, exist_ok=True)
        inputs.write_text(text)

    def test_class_1300_placeholder_is_not_reported_unclaimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_pci(root, "0000:00:08.0", "0x130000")
            rows = self.run_check(root, "check_hardware_unclaimed_pci")
        status, _, message = self.one(rows, "hw/unclaimed-pci")
        self.assertEqual(status, "PASS")
        self.assertNotIn("0000:00:08.0", message)
        self.assertIn("no unexpected", message)

    def test_class_1300_exclusion_does_not_hide_actionable_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_pci(root, "0000:00:08.0", "0x130000")
            self.make_pci(root, "0000:03:00.0", "0x030000")
            rows = self.run_check(root, "check_hardware_unclaimed_pci")
        status, _, message = self.one(rows, "hw/unclaimed-pci")
        self.assertEqual(status, "WARN")
        self.assertIn("0000:03:00.0", message)
        self.assertNotIn("0000:00:08.0", message)

    def test_desktop_day_one_inventory_skips_laptop_only_expectations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_inputs(root, 'N: Name="Dell KB216 Wired Keyboard"\n')
            rows = self.run_check(root, "check_hardware_day_one")
        status, _, message = self.one(rows, "hw/day-one")
        self.assertEqual(status, "SKIP")
        self.assertIn("non-laptop venue", message)
        for device in ("battery", "backlight", "wifi", "bluetooth", "usb-c", "camera"):
            self.assertNotIn(device, message)

    def test_existing_internal_keyboard_cue_selects_laptop_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_inputs(root, 'N: Name="AT Translated Set 2 keyboard"\n')
            rows = self.run_check(root, "check_hardware_day_one")
        status, _, message = self.one(rows, "hw/day-one")
        self.assertEqual(status, "WARN")
        self.assertIn("battery", message)

    def test_existing_touchpad_cue_also_selects_laptop_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_inputs(root, 'N: Name="ELAN0788 Touchpad"\n')
            rows = self.run_check(root, "check_hardware_day_one")
        self.assertEqual(self.one(rows, "hw/day-one")[0], "WARN")

    def test_unknown_venue_is_not_treated_as_desktop_or_laptop(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self.run_check(Path(tmp), "check_hardware_day_one")
        status, _, message = self.one(rows, "hw/day-one")
        self.assertEqual(status, "SKIP")
        self.assertIn("cannot determine venue", message)

    def test_failed_deferred_probe_read_never_passes_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sys/kernel/debug/devices_deferred").mkdir(parents=True)
            rows = self.run_check(root, "check_hardware_deferred_probe")
        status, _, message = self.one(rows, "hw/deferred")
        self.assertEqual(status, "WARN")
        self.assertIn("could not read", message)


if __name__ == "__main__":
    unittest.main()
