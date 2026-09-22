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

    def test_an_empty_inventory_says_so_instead_of_answering_silently(self):
        """lspci exiting 0 with no output is not the same fact as a machine
        without this device, and it is not a fact about any real machine: every
        machine the installer runs on has PCI devices. The answer is still no —
        fail-closed — but it is said out loud, because an empty listing that
        passes silently is how a broken inventory reads as a correct one."""
        with self.assertLogs("forge.packages", level="INFO") as logs:
            answer = packages.target_has_pci_device(
                "17a0", "9755", runner=_runner_returning(""))
        self.assertFalse(answer)
        self.assertTrue(
            any("listed no PCI devices" in line for line in logs.output),
            logs.output)

    def test_a_listing_of_blank_lines_says_so_too(self):
        """One newline is not an inventory either, and it is not empty.

        Found by the second read of this change: the first form of it asked
        whether the LINE LIST was empty, and standard output holding a single
        newline splits into a list of one empty string, which is not empty. So
        the branch was skipped, every membership test was false, and the
        answer was no in silence — the one reading this change exists to name.
        The question is what the listing PARSES to, not how many lines it
        printed.
        """
        for stdout in ("\n", "\n\n\n", "   \n", "   "):
            with self.subTest(stdout=stdout):
                with self.assertLogs("forge.packages", level="INFO") as logs:
                    answer = packages.target_has_pci_device(
                        "17a0", "9755", runner=_runner_returning(stdout))
                self.assertFalse(answer)
                self.assertTrue(
                    any("listed no PCI devices" in line
                        for line in logs.output), logs.output)

    def test_a_listing_of_unparseable_lines_says_so_too(self):
        """Output that is not an lspci listing at all names no device.

        Two fields where the format has three or more is not a device
        identity; a listing made only of such lines holds no identity, and the
        same rule covers it without a case of its own.
        """
        with self.assertLogs("forge.packages", level="INFO") as logs:
            answer = packages.target_has_pci_device(
                "17a0", "9755",
                runner=_runner_returning("garbage here\nmore garbage\n"))
        self.assertFalse(answer)
        self.assertTrue(
            any("listed no PCI devices" in line for line in logs.output),
            logs.output)

    def test_a_real_listing_without_the_device_stays_silent(self):
        """A machine that really does not have the device is a true reading
        and must not speak: only an impossible one does."""
        with self.assertNoLogs("forge.packages", level="INFO"):
            answer = packages.target_has_pci_device(
                "17a0", "9755",
                runner=_runner_returning(
                    "00:00.0 0600: 8086:4601 (rev 04)\n"
                    "01:00.0 0300: 10de:24a0 (rev a1)\n"))
        self.assertFalse(answer)

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


class ALineThatNamesNoDeviceIsNotAnIdentity(unittest.TestCase):
    """Three or more fields is not the same as a device identity.

    The reader takes the third whitespace-separated field of an `lspci -n`
    line. Taking it by POSITION alone made any line with three or more fields
    yield an identity, whatever stood there: a permission error, one of
    lspci's own diagnostic messages, or a device line whose identity is
    malformed. The identity list was then not empty, the check that names an
    impossible reading did not fire, the membership test was false, and the
    answer was no with nothing said — the same reading this change exists to
    name, reached through a different door. The field is an identity only when
    it HAS the shape of one: four hexadecimal digits, a colon, four
    hexadecimal digits.

    The three listings below were measured by the independent read of the
    previous form of this change (2026-09-22); the two-field listing is the
    case that was already covered and is kept here beside them.
    """

    LISTINGS_THAT_NAME_NO_DEVICE = (
        "01:00.0 0300: not-an-identity\n",
        "cannot open /sys/bus/pci: Permission denied\n",
        "lspci: Unable to load libkmod resources\n",
        "garbage here\nmore garbage\n",
        "01:00.0 0300: notahexpair (rev a1)\n",
        "01:00.0 0300: 10d:24a (rev a1)\n",
    )

    def test_the_predicate_answers_no_and_says_so(self):
        for stdout in self.LISTINGS_THAT_NAME_NO_DEVICE:
            with self.subTest(stdout=stdout):
                with self.assertLogs("forge.packages", level="INFO") as logs:
                    answer = packages.target_has_pci_device(
                        "17a0", "9755", runner=_runner_returning(stdout))
                self.assertFalse(answer)
                self.assertTrue(
                    any("listed no PCI devices" in line
                        for line in logs.output), logs.output)

    def test_the_parser_returns_nothing_for_them(self):
        for stdout in self.LISTINGS_THAT_NAME_NO_DEVICE:
            for line in stdout.splitlines():
                with self.subTest(line=line):
                    self.assertIsNone(packages._pci_id_of(line))

    def test_a_well_formed_identity_is_still_read(self):
        """The shape check must not reject the lines it exists to admit."""
        self.assertEqual(
            packages._pci_id_of("01:00.0 0300: 10de:2484 (rev a1)"),
            "10de:2484")
        self.assertEqual(
            packages._pci_id_of("2D:00.0 0805: 17A0:9755 (rev 01)"),
            "17a0:9755")

    def test_one_real_device_line_among_them_is_still_found(self):
        """A listing that names a device is a true reading and stays silent,
        even when unreadable lines sit beside it."""
        stdout = ("lspci: Unable to load libkmod resources\n"
                  "2d:00.0 0805: 17a0:9755 (rev 01)\n")
        with self.assertNoLogs("forge.packages", level="INFO"):
            answer = packages.target_has_pci_device(
                "17a0", "9755", runner=_runner_returning(stdout))
        self.assertTrue(answer)


class TheDisplayGateReadsIdentitiesToo(unittest.TestCase):
    """The other consumer of the same reader parses the same field.

    detect_display_pci_vendors does not call the identity parser; it reads the
    class field and the vendor half of the third field itself. Measured on the
    tip before this change, a listing whose only line was
    "01:00.0 0300: not-an-identity" gave the vendor set {"not-an-identity"} —
    a non-empty set, which every caller reads as "this machine was examined
    and these are its display vendors". No gated package can match such a
    vendor, so nothing is installed that should not be; what is wrong is that
    a listing naming no device is indistinguishable from one that does. Both
    consumers now decide on identities that have the shape of one.
    """

    def setUp(self):
        packages._PCI_VENDOR_CACHE = None
        self.addCleanup(setattr, packages, "_PCI_VENDOR_CACHE", None)

    def test_a_display_line_with_a_malformed_identity_names_no_vendor(self):
        for stdout in ("01:00.0 0300: not-an-identity\n",
                       "01:00.0 0300: notahexpair (rev a1)\n",
                       "01:00.0 0300: 10d:24a (rev a1)\n"):
            with self.subTest(stdout=stdout):
                packages._PCI_VENDOR_CACHE = None
                with mock.patch.object(packages.subprocess, "run",
                                       _runner_returning(stdout)):
                    self.assertEqual(packages.detect_display_pci_vendors(),
                                     set())

    def test_the_real_listing_is_read_exactly_as_before(self):
        with mock.patch.object(packages.subprocess, "run",
                               _runner_returning(_INVENTORY)):
            self.assertEqual(packages.detect_display_pci_vendors(),
                             {"8086", "10de"})


if __name__ == "__main__":
    unittest.main()
