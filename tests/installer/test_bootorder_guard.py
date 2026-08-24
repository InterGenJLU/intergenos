"""Boot-order guard — the guard must be able to READ the firmware.

WHY THIS FILE EXISTS. `installer/bootorder/bootorder-check.sh` runs on every boot
to keep the registered UEFI boot entry at the front of BootOrder. On a release
install it printed one line every boot —

    intergenos-bootorder-check: cannot determine boot order:
    /usr/bin/efibootmgr is not executable.

— and exited 0, so the unit finished successfully and neither `systemctl --failed`
nor `systemctl is-system-running` showed anything. Meanwhile the firmware had done
exactly what the guard exists to catch: it moved the removable-media fallback
loader to the front of BootOrder, and the machine was booting through it.

The cause is a single default: the guard looks for efibootmgr in `/usr/bin`, while
the tree's own `packages/core/efibootmgr` recipe installs it in `/usr/sbin`.

The first test below is the one that fails on the shipped guard. It does not
hardcode a path of its own — it reads the guard's default and checks it against
the efibootmgr recipe's declared `verify_paths`, so the tree's own statement of
where the binary lands is the oracle and the two cannot drift apart again.

The remaining tests are the behavioural net: they drive the guard against a fake
efibootmgr and pin detect / no-change / report-only / repair / repair-refused, so
a future edit cannot quietly lose any of them.
"""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "installer" / "bootorder" / "bootorder-check.sh"
EFIBOOTMGR_RECIPE = REPO / "packages" / "core" / "efibootmgr" / "package.yml"

# What a firmware that has demoted our entry looks like: "UEFI OS" (the
# removable-media fallback loader on the same ESP) sits ahead of "InterGenOS".
NVRAM_DEMOTED = """\
BootCurrent: 0003
Timeout: 1 seconds
BootOrder: 0003,0000,0001,0002
Boot0000* InterGenOS\tHD(1,GPT,abcd,0x800,0x200000)/\\EFI\\INTERGENOS\\SHIMX64.EFI
Boot0001* rEFInd Boot Manager\tVenHw(99e2)
Boot0002* ubuntu\tVenHw(99e2)
Boot0003* UEFI OS\tHD(1,GPT,abcd,0x800,0x200000)/\\EFI\\BOOT\\BOOTX64.EFI
"""

NVRAM_OURS_FIRST = NVRAM_DEMOTED.replace(
    "BootOrder: 0003,0000,0001,0002", "BootOrder: 0000,0003,0001,0002")


def declared_efibootmgr_paths():
    """Every path the efibootmgr recipe says it installs, from the recipe itself."""
    text = EFIBOOTMGR_RECIPE.read_text()
    block = text.split("verify_paths:", 1)[1]
    paths = []
    for line in block.splitlines():
        s = line.strip()
        if s.startswith("- "):
            paths.append(s[2:].strip())
        elif s and not s.startswith("#") and not s.startswith("- "):
            break
    return paths


def guard_default_efibootmgr_paths():
    """Every absolute efibootmgr path the guard would try by default."""
    text = GUARD.read_text()
    found = []
    for m in re.finditer(r'^EFIBOOTMGR(?:_CANDIDATES)?="([^"]*)"', text, re.M):
        for token in m.group(1).split():
            if token.startswith("/"):
                found.append(token)
    return found


class TestGuardCanReachTheBinaryTheTreeInstalls(unittest.TestCase):
    """The red test: the guard's default must name a path the tree installs."""

    def test_default_path_matches_the_efibootmgr_recipe(self):
        declared = declared_efibootmgr_paths()
        self.assertIn("/usr/sbin/efibootmgr", declared,
                      "the efibootmgr recipe no longer declares the binary it "
                      "installs; this test's oracle has moved")
        defaults = guard_default_efibootmgr_paths()
        self.assertTrue(defaults, "the guard names no efibootmgr path at all")
        overlap = set(defaults) & set(declared)
        self.assertTrue(
            overlap,
            "the boot-order guard would look for efibootmgr at "
            f"{defaults} but packages/core/efibootmgr installs it at {declared}. "
            "The guard cannot read the firmware, and it exits 0 while blind.")


class GuardRun(unittest.TestCase):
    """Drive the real script against a fake efibootmgr in a sandbox."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.efi_dir = self.tmp / "sys-firmware-efi"
        self.efi_dir.mkdir()
        self.state = self.tmp / "nvram.txt"
        self.written = self.tmp / "written.txt"
        self.intent = self.tmp / "boot-default.conf"
        self.intent.write_text(
            "default_boot_target=yes\nboot_entry_label=InterGenOS\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fake_efibootmgr(self, *, accept_write=True, honour_write=True):
        """A stand-in that reads NVRAM from a file and records any -o write."""
        path = self.tmp / "efibootmgr"
        path.write_text(f"""#!/bin/bash
STATE="{self.state}"
WRITTEN="{self.written}"
if [ "${{1:-}}" = "-o" ]; then
    echo "$2" >> "$WRITTEN"
    {'[ "$?" = 0 ]' if accept_write else 'exit 5'}
    {f'''NEW="$2"
    sed -i "s/^BootOrder: .*/BootOrder: $NEW/" "$STATE"''' if honour_write else ': "firmware ignored the write"'}
fi
cat "$STATE"
""")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    def run_guard(self, nvram, *extra, accept_write=True, honour_write=True,
                  intent="yes"):
        self.state.write_text(nvram)
        self.intent.write_text(
            f"default_boot_target={intent}\nboot_entry_label=InterGenOS\n")
        fake = self.fake_efibootmgr(accept_write=accept_write,
                                    honour_write=honour_write)
        cmd = ["bash", str(GUARD),
               "--efi-dir", str(self.efi_dir),
               "--intent-file", str(self.intent),
               "--efibootmgr", str(fake), *extra]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    # --- the positive control: it must SEE the real condition ---------------
    def test_detects_a_demoted_entry(self):
        r = self.run_guard(NVRAM_DEMOTED, "--dry-run")
        self.assertIn("the firmware moved it", r.stdout)
        self.assertIn("Restoring: 0000,0003,0001,0002", r.stdout)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.written.exists(), "--dry-run wrote to NVRAM")

    # --- the negative control: it must NOT cry wolf --------------------------
    def test_reports_no_change_when_our_entry_is_first(self):
        r = self.run_guard(NVRAM_OURS_FIRST, "--dry-run")
        self.assertIn("is first — no change", r.stdout)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.written.exists())

    def test_reports_only_when_the_install_chose_another_default(self):
        r = self.run_guard(NVRAM_DEMOTED, intent="no")
        self.assertIn("reporting only, nothing changed", r.stdout)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.written.exists(), "wrote NVRAM against the "
                                                "recorded install intent")

    # --- the repair leg, both outcomes --------------------------------------
    def test_repairs_and_verifies_the_read_back(self):
        r = self.run_guard(NVRAM_DEMOTED)
        self.assertIn("repaired", r.stdout)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.written.read_text().strip(), "0000,0003,0001,0002")

    def test_fails_loudly_when_the_firmware_ignores_the_write(self):
        r = self.run_guard(NVRAM_DEMOTED, honour_write=False)
        self.assertIn("still not first", r.stdout)
        self.assertEqual(r.returncode, 1,
                         "a repair that did not take must be a failed unit")

    # --- the blind case must stay honest, and stay non-fatal ----------------
    def test_missing_binary_reports_undetermined_and_does_not_claim_a_pass(self):
        self.state.write_text(NVRAM_DEMOTED)
        cmd = ["bash", str(GUARD),
               "--efi-dir", str(self.efi_dir),
               "--intent-file", str(self.intent),
               "--efibootmgr", str(self.tmp / "does-not-exist")]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        self.assertIn("cannot determine boot order", r.stdout)
        self.assertNotIn("no change", r.stdout)
        self.assertNotIn("repaired", r.stdout)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
