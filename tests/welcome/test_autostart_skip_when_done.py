# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Welcomer's autostart must not launch a process that exits at once.

WHAT THIS FIXES. On an already-set-up system the Welcomer's wrapper finds the
per-user done-marker and exits 0 within milliseconds. On one of three cold
boots of an installed machine, systemd lost the race to account for that
process and the unit ended `result=resources` with "No PIDs left". No user
unit failed and nothing was broken, but a service that reports a resource
failure on a normal boot is noise in the one place a person looks when
something IS wrong.

THE MECHANISM, MEASURED — not assumed. On GNOME 49.2 there is no
gnome-session-binary: /etc/xdg/autostart entries are converted by
systemd-xdg-autostart-generator into user services. For this entry the
generated unit is Type=exec with ExitType=cgroup, which is precisely the
combination that has to observe a process that may already be gone.

Two remedies were measured and rejected before this one:

  * `AutostartCondition=unless-exists intergen-welcome/done`, the key
    gnome-initial-setup uses, is INERT here. The generator delegates that key
    to `gnome-systemd-autostart-condition`, which this system does not ship;
    the generator then writes "ExecCondition using
    gnome-systemd-autostart-condition skipped due to missing binary" and runs
    the entry unconditionally. Adding the key would have looked like a fix and
    changed nothing.
  * Delaying the wrapper's exit would hide the race rather than remove it.

THE REMEDY. The package ships a systemd drop-in for the generated unit
carrying `ConditionPathExists=!%h/.config/intergen-welcome/done`. When the
marker is there the unit is skipped cleanly — inactive, Result=success, no
process started — so there is no fast-exiting process for systemd to lose.
Measured on systemd 259.1: the drop-in is found and merged onto the
generator-produced unit, and the skip is logged as an unmet condition, not a
failure.

The wrapper's own marker check stays exactly as it was. It remains the
authority — it also covers the app-grid --force path and the live-ISO guard,
which no unit condition can see — and it means that if the drop-in ever stops
matching (a renamed desktop file changes the generated unit name), behaviour
is unchanged and only the race returns. The unit-name test below is what
catches that rename.
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_SH = REPO_ROOT / "packages/desktop/intergen-welcome/build.sh"
PKG_YML = REPO_ROOT / "packages/desktop/intergen-welcome/package.yml"

# The marker the wrapper gates on. One string, asserted in both places, so the
# unit condition and the wrapper cannot drift apart.
MARKER_RELATIVE = ".config/intergen-welcome/done"

AUTOSTART_DESKTOP_BASENAME = "intergen-welcome"
# systemd-xdg-autostart-generator names the unit
# app-<systemd-escaped basename>@autostart.service.
EXPECTED_UNIT = r"app-intergen\x2dwelcome@autostart.service"
EXPECTED_DROPIN_DIR = f"/usr/lib/systemd/user/{EXPECTED_UNIT}.d"


class GeneratedUnitName(unittest.TestCase):

    def test_the_expected_unit_name_is_what_systemd_escaping_produces(self):
        """Derived, not copied. A renamed autostart entry changes the unit
        name, the drop-in stops applying, and this test is what says so."""
        if not shutil.which("systemd-escape"):
            self.skipTest("systemd-escape not present on this host")
        escaped = subprocess.run(
            ["systemd-escape", AUTOSTART_DESKTOP_BASENAME],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(f"app-{escaped}@autostart.service", EXPECTED_UNIT)

    def test_the_recipe_still_installs_that_autostart_entry_basename(self):
        text = BUILD_SH.read_text()
        self.assertIn(f"{AUTOSTART_DESKTOP_BASENAME}.desktop", text)
        self.assertIn('autostartdir="${DESTDIR}/etc/xdg/autostart"', text)


class DropInIsShipped(unittest.TestCase):

    def setUp(self):
        self.text = BUILD_SH.read_text()

    def test_the_recipe_installs_a_drop_in_for_the_generated_unit(self):
        self.assertIn(EXPECTED_DROPIN_DIR.replace("/usr/lib", "${DESTDIR}/usr/lib"),
                      self.text.replace("\\\\", "\\"),
                      "no drop-in directory for the generated autostart unit")

    def test_the_drop_in_skips_the_unit_when_the_marker_exists(self):
        self.assertIn(f"ConditionPathExists=!%h/{MARKER_RELATIVE}", self.text,
                      "the drop-in does not condition on the done-marker")

    def test_the_condition_and_the_wrapper_gate_on_the_same_marker(self):
        """If these two ever name different files, the unit would skip runs
        the wrapper wanted, or run ones it did not."""
        self.assertIn('done_marker="${HOME}/.config/intergen-welcome/done"',
                      self.text,
                      "the wrapper's marker path changed; the unit condition "
                      "must change with it")

    def test_the_wrapper_marker_check_is_still_there(self):
        """The drop-in removes the race; it does not take over the gate. The
        wrapper still covers the app-grid --force path and the live-ISO
        guard, which no unit condition can see."""
        self.assertRegex(
            self.text,
            r'if \[ "\$\{force_run\}" -eq 0 \] && \[ -e "\$\{done_marker\}" \]',
            "the wrapper's own marker gate was removed")

    def test_the_drop_in_is_declared_as_a_load_bearing_path(self):
        yml = PKG_YML.read_text()
        self.assertIn(EXPECTED_DROPIN_DIR, yml,
                      "the drop-in is not in verify_paths, so a build that "
                      "failed to ship it would pass the pre-squashfs audit")

    def test_the_release_was_bumped_for_the_shipped_file_change(self):
        m = re.search(r"^release:\s*(\d+)", PKG_YML.read_text(), re.M)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 32)


class TheRejectedRemedyIsNotSilentlyReintroduced(unittest.TestCase):
    """AutostartCondition reads like the obvious fix and does nothing here.
    A future edit that adds it is a change that has to be re-measured."""

    def test_no_autostart_condition_key_is_shipped(self):
        # The KEY, at the start of a line inside a written desktop entry —
        # not the word, which the comment explaining the rejection uses.
        self.assertNotRegex(BUILD_SH.read_text(),
                            r"(?m)^AutostartCondition=")


if __name__ == "__main__":
    unittest.main()
