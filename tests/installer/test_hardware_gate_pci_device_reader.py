# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The installer asks the machine what PCI devices it has in ONE place.

Two questions are now asked of `lspci -n` during an install: which display
vendors are present (the package hardware gate) and whether a particular
vendor:device pair is present (the card-reader boot parameter). They share one
reader so that there is one fail-closed rule, one log wording and one command
to audit.

The display gate's own behaviour is asserted here as well, because it was
rewritten onto the shared reader and its four callers depend on it being
unchanged: same answers, same memoisation, same empty set when the machine
cannot be read.
"""

import subprocess
import unittest
from unittest import mock

from installer.backend import packages


class _FakeCompleted:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


_INVENTORY = "\n".join([
    "00:00.0 0600: 8086:4601 (rev 04)",
    "00:02.0 0300: 8086:46a8 (rev 0c)",
    "01:00.0 0300: 10de:24a0 (rev a1)",
    "2d:00.0 0805: 17a0:9755 (rev 01)",
])


def _runner_returning(stdout, returncode=0):
    return lambda *a, **kw: _FakeCompleted(stdout, returncode)


def _runner_raising(exc):
    def _run(*a, **kw):
        raise exc
    return _run


class PciDevicePredicate(unittest.TestCase):

    def test_it_finds_a_device_that_is_present(self):
        self.assertTrue(packages.target_has_pci_device(
            "17a0", "9755", runner=_runner_returning(_INVENTORY)))

    def test_it_does_not_find_a_device_that_is_absent(self):
        self.assertFalse(packages.target_has_pci_device(
            "1217", "8621", runner=_runner_returning(_INVENTORY)))

    def test_the_vendor_alone_is_not_enough(self):
        self.assertFalse(packages.target_has_pci_device(
            "17a0", "9750", runner=_runner_returning(_INVENTORY)))

    def test_case_does_not_matter(self):
        self.assertTrue(packages.target_has_pci_device(
            "17A0", "9755", runner=_runner_returning(_INVENTORY)))

    def test_it_answers_no_when_lspci_is_absent(self):
        with self.assertLogs("forge.packages", level="WARNING") as logs:
            answer = packages.target_has_pci_device(
                "17a0", "9755",
                runner=_runner_raising(FileNotFoundError("lspci")))
        self.assertFalse(answer)
        self.assertTrue(any("lspci unavailable" in line for line in logs.output))

    def test_it_answers_no_when_lspci_fails(self):
        with self.assertLogs("forge.packages", level="WARNING") as logs:
            answer = packages.target_has_pci_device(
                "17a0", "9755", runner=_runner_returning("", returncode=7))
        self.assertFalse(answer)
        self.assertTrue(any("lspci exited 7" in line for line in logs.output))

    def test_a_short_line_is_skipped_not_fatal(self):
        answer = packages.target_has_pci_device(
            "17a0", "9755",
            runner=_runner_returning("garbage\n\n" + _INVENTORY))
        self.assertTrue(answer)

    def test_it_is_not_memoised(self):
        """The display gate caches because it is asked once per group during
        one install. This predicate must not inherit that cache, or a second
        question about a different device would be answered from the first."""
        self.assertTrue(packages.target_has_pci_device(
            "17a0", "9755", runner=_runner_returning(_INVENTORY)))
        self.assertFalse(packages.target_has_pci_device(
            "17a0", "9755", runner=_runner_returning("")))


class TheDisplayGateIsUnchanged(unittest.TestCase):

    def setUp(self):
        packages._PCI_VENDOR_CACHE = None
        self.addCleanup(setattr, packages, "_PCI_VENDOR_CACHE", None)

    def test_it_returns_display_vendors_only(self):
        with mock.patch.object(packages.subprocess, "run",
                               _runner_returning(_INVENTORY)):
            self.assertEqual(packages.detect_display_pci_vendors(),
                             {"8086", "10de"})

    def test_the_storage_class_device_is_not_a_display_vendor(self):
        with mock.patch.object(packages.subprocess, "run",
                               _runner_returning(_INVENTORY)):
            self.assertNotIn("17a0", packages.detect_display_pci_vendors())

    def test_it_is_still_memoised(self):
        with mock.patch.object(packages.subprocess, "run",
                               _runner_returning(_INVENTORY)):
            first = packages.detect_display_pci_vendors()
        with mock.patch.object(packages.subprocess, "run",
                               _runner_returning("")):
            second = packages.detect_display_pci_vendors()
        self.assertEqual(first, second)

    def test_it_fails_closed_when_lspci_is_absent(self):
        with mock.patch.object(packages.subprocess, "run",
                               _runner_raising(FileNotFoundError("lspci"))):
            with self.assertLogs("forge.packages", level="WARNING") as logs:
                self.assertEqual(packages.detect_display_pci_vendors(), set())
        self.assertTrue(any("gated packages will be skipped (fail-closed)"
                            in line for line in logs.output))

    def test_it_fails_closed_when_lspci_exits_nonzero(self):
        with mock.patch.object(packages.subprocess, "run",
                               _runner_returning("", returncode=1)):
            with self.assertLogs("forge.packages", level="WARNING") as logs:
                self.assertEqual(packages.detect_display_pci_vendors(), set())
        self.assertTrue(any("lspci exited 1" in line for line in logs.output))

    def test_a_subprocess_error_is_caught(self):
        with mock.patch.object(
            packages.subprocess, "run",
            _runner_raising(subprocess.TimeoutExpired(["lspci"], 10))
        ):
            with self.assertLogs("forge.packages", level="WARNING"):
                self.assertEqual(packages.detect_display_pci_vendors(), set())


if __name__ == "__main__":
    unittest.main()
