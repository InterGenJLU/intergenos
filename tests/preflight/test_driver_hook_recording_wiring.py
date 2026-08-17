# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every build driver that runs a recipe's post_install must record what it did.

The recording only works if it brackets the hook: the baseline has to be taken
BEFORE post_install runs and the comparison AFTER it succeeds. A driver that
calls neither silently produces exactly the defect this mechanism exists to
remove — payload files a hook rewrote, recorded as ordinary content, on a
shipped image — and nothing in a unit test of the recording functions would
notice, because the functions themselves would still pass.

So the wiring is asserted here, per driver, by reading the drivers. A new
driver that runs post_install without the brackets fails this test rather than
shipping a quietly wrong image.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPTS = REPO / "scripts"

POST_INSTALL_CALL = 'pkg_run_phase post_install "$pkg_log"'


def drivers_running_post_install():
    """Every chroot-build driver that invokes the post_install phase runner.

    Derived by READING the scripts, not from a hand-kept list: a list would go
    stale the first time a driver is added, which is precisely the case this
    test exists to catch.
    """
    found = []
    for path in sorted(SCRIPTS.glob("chroot-build-*.sh")):
        if POST_INSTALL_CALL in path.read_text():
            found.append(path)
    return found


class DriverWiringTests(unittest.TestCase):

    def test_the_sweep_finds_the_drivers_it_is_supposed_to_guard(self):
        names = {p.name for p in drivers_running_post_install()}
        self.assertTrue(
            names,
            "no driver was found to run post_install — the search string is "
            "stale and this whole test file is passing by vacuity")
        for expected in ("chroot-build-base.sh", "chroot-build-ch8.sh",
                         "chroot-build-ch10.sh", "chroot-build-core-extra.sh"):
            self.assertIn(expected, names)

    def test_shared_functions_exist(self):
        text = (SCRIPTS / "pkg-functions.sh").read_text()
        for fn in ("pkg_hook_baseline()", "pkg_record_hook_changes()"):
            self.assertIn(
                fn, text,
                f"{fn} must be defined once in pkg-functions.sh so every "
                f"driver follows one rule rather than four copies of it")

    def test_every_driver_brackets_its_post_install(self):
        for path in drivers_running_post_install():
            text = path.read_text()
            with self.subTest(driver=path.name):
                self.assertIn(
                    'pkg_hook_baseline "$name"', text,
                    "the driver runs post_install but never takes a baseline, "
                    "so any file the hook rewrites stays recorded as ordinary "
                    "payload and the image metadata gate refuses the build")
                self.assertIn('pkg_record_hook_changes "$name"', text)

                base_at = text.index('pkg_hook_baseline "$name"')
                hook_at = text.index(POST_INSTALL_CALL)
                record_at = text.index('pkg_record_hook_changes "$name"')
                self.assertLess(
                    base_at, hook_at,
                    "the baseline must be captured BEFORE the hook runs — "
                    "taken afterwards it records the post-hook bytes and the "
                    "comparison can never see a change")
                self.assertLess(
                    hook_at, record_at,
                    "the comparison must run AFTER the hook")

    def test_the_recording_is_not_reached_when_post_install_failed(self):
        """A failed hook must return before the recording, not through it."""
        for path in drivers_running_post_install():
            text = path.read_text()
            with self.subTest(driver=path.name):
                record_at = text.index('pkg_record_hook_changes "$name"')
                # The driver's own failure branch for the hook: it returns
                # before reaching the recording.
                fail_branch = re.search(
                    r'if \[ "\$pi_rc" -ne 0 \];.*?\n\s*fi', text, re.S)
                self.assertIsNotNone(
                    fail_branch,
                    "the driver has no halt branch for a failing post_install")
                self.assertLess(
                    fail_branch.end(), record_at,
                    "the recording must sit after the failure branch — "
                    "recording against a half-run hook would attribute an "
                    "incomplete state to a successful one")


if __name__ == "__main__":
    unittest.main()
