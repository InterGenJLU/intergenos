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

# Readable listing lines, for the cases that place a line that cannot be read
# beside lines that can: a host bridge, an Intel display controller and an
# NVIDIA one.
_HOST = "00:00.0 0600: 8086:9b61\n"
_INTEL = "00:02.0 0300: 8086:46a8 (rev 0c)\n"
_NVIDIA = "01:00.0 0300: 10de:2484 (rev a1)\n"


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

    def setUp(self):
        packages._PCI_VENDOR_CACHE = None
        self.addCleanup(setattr, packages, "_PCI_VENDOR_CACHE", None)

    def _read(self, stdout):
        packages._PCI_VENDOR_CACHE = None
        with mock.patch.object(packages.subprocess, "run",
                               _runner_returning(stdout)):
            return packages.detect_display_pci_vendors()

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

    def test_the_display_gate_names_no_vendor_for_them_and_says_so(self):
        """The same listings through the other check. These shapes are
        asserted through the two checks the installer runs: they were once
        asserted only through a line parser that no installer code called
        after the slot rule, so a check that stopped using the shared reader
        would have left those assertions green."""
        for stdout in self.LISTINGS_THAT_NAME_NO_DEVICE:
            with self.subTest(stdout=stdout):
                with self.assertLogs("forge.packages", level="INFO") as logs:
                    self.assertEqual(self._read(stdout), set())
                self.assertTrue(
                    any("listed no PCI devices" in line
                        for line in logs.output), logs.output)

    def test_a_well_formed_identity_is_still_read(self):
        """The shape check must not reject the lines it exists to admit, on
        either check, and a true reading stays silent."""
        for line, vendor, device, display_vendors in (
                ("01:00.0 0300: 10de:2484 (rev a1)\n", "10de", "2484",
                 {"10de"}),
                ("2D:00.0 0805: 17A0:9755 (rev 01)\n", "17a0", "9755",
                 set())):
            with self.subTest(line=line):
                with self.assertNoLogs("forge.packages", level="INFO"):
                    self.assertTrue(packages.target_has_pci_device(
                        vendor, device, runner=_runner_returning(line)))
                    self.assertEqual(self._read(line), display_vendors)

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

    detect_display_pci_vendors did not call the identity parser; it read the
    class field and the vendor half of the third field itself. Measured on the
    tip before this change, a listing whose only line was
    "01:00.0 0300: not-an-identity" gave the vendor set {"not-an-identity"} —
    a non-empty set, which every caller reads as "this machine was examined
    and these are its display vendors". Worse, measured by the independent
    read of that form: a malformed display line beginning with a gated
    vendor's code — "10de:2484junk", "10de:zzzz", "10de:" — gave {"10de"} and
    KEPT that vendor's gated packages. Both consumers now decide on identities
    that have the shape of one, and the display gate names the readings that
    leave its set empty although the listing was read.
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

    def test_a_malformed_line_beginning_with_a_gated_vendor_names_no_vendor(self):
        """The case that changed what was installed: at the parent of this
        change each of these gave {"10de"} and kept nvidia and lib32-nvidia."""
        for stdout in ("01:00.0 0300: 10de:2484junk (rev a1)\n",
                       "01:00.0 0300: 10de:zzzz (rev a1)\n",
                       "01:00.0 0300: 10de:\n"):
            with self.subTest(stdout=stdout):
                packages._PCI_VENDOR_CACHE = None
                with mock.patch.object(packages.subprocess, "run",
                                       _runner_returning(stdout)):
                    self.assertEqual(packages.detect_display_pci_vendors(),
                                     set())


class TheDisplayGateNamesAnEmptyReading(unittest.TestCase):
    """An empty vendor set has three causes once the listing was read, and the
    install record must say which one it was.

    Found by the independent read of the previous form of this change: after
    malformed identities stopped naming vendors, a listing whose display lines
    were all malformed left exactly the same record as a machine with no
    display device, and a listing naming no device at all did too. The device
    predicate already names its own empty reading; the display gate now names
    both of its own. The answer is unchanged in every case: an empty set, and
    the gated packages skipped.
    """

    def setUp(self):
        packages._PCI_VENDOR_CACHE = None
        self.addCleanup(setattr, packages, "_PCI_VENDOR_CACHE", None)

    def _read(self, stdout):
        with mock.patch.object(packages.subprocess, "run",
                               _runner_returning(stdout)):
            return packages.detect_display_pci_vendors()

    def test_a_listing_that_names_no_device_says_so(self):
        for stdout in ("", "\n", "   \n", "garbage here\nmore garbage\n",
                       "cannot open /sys/bus/pci: Permission denied\n"):
            with self.subTest(stdout=stdout):
                packages._PCI_VENDOR_CACHE = None
                with self.assertLogs("forge.packages", level="INFO") as logs:
                    self.assertEqual(self._read(stdout), set())
                self.assertTrue(
                    any("listed no PCI devices" in line
                        and "gated packages will be skipped" in line
                        for line in logs.output), logs.output)
                self.assertFalse(
                    any("display-class line" in line for line in logs.output),
                    "a listing with no display-class line said it had one: "
                    "%s" % logs.output)

    def test_a_listing_whose_only_line_is_a_malformed_display_line_says_both(
            self):
        """Both facts hold, so both lines are written; this is what keeps it
        apart from a listing that holds nothing at all."""
        with self.assertLogs("forge.packages", level="INFO") as logs:
            self.assertEqual(self._read("01:00.0 0300: not-an-identity\n"),
                             set())
        self.assertTrue(any("listed no PCI devices" in line
                            for line in logs.output), logs.output)
        self.assertTrue(
            any("1 display-class line(s) read and none carries a device "
                "identity" in line for line in logs.output), logs.output)

    def test_display_lines_that_carry_no_identity_say_so(self):
        non_display = "00:00.0 0600: 8086:4601 (rev 04)\n"
        for display in ("01:00.0 0300: not-an-identity\n",
                        "01:00.0 0300: 10de:2484junk (rev a1)\n",
                        "01:00.0 0300:\n"):
            with self.subTest(display=display):
                packages._PCI_VENDOR_CACHE = None
                with self.assertLogs("forge.packages", level="INFO") as logs:
                    self.assertEqual(self._read(non_display + display), set())
                self.assertTrue(
                    any("1 display-class line(s) read and none carries a "
                        "device identity" in line for line in logs.output),
                    logs.output)
                self.assertFalse(
                    any("listed no PCI devices" in line
                        for line in logs.output),
                    "a listing that names a device said it named none: "
                    "%s" % logs.output)

    def test_a_machine_with_no_display_device_stays_silent(self):
        """That empty set is a true reading, so nothing is said about it."""
        listing = ("00:00.0 0600: 8086:4601 (rev 04)\n"
                   "2d:00.0 0805: 17a0:9755 (rev 01)\n")
        with self.assertNoLogs("forge.packages", level="INFO"):
            self.assertEqual(self._read(listing), set())

    def test_a_real_listing_with_display_devices_stays_silent(self):
        with self.assertNoLogs("forge.packages", level="INFO"):
            self.assertEqual(self._read(_INVENTORY), {"8086", "10de"})

    def test_one_valid_display_line_beside_a_malformed_one_says_so(self):
        """The vendors that were read are the answer, and the record says
        that one display line was not read. This listing stayed silent before:
        a vendor was read, so the partial reading passed as a whole one."""
        listing = _INVENTORY + "\n03:00.0 0300: not-an-identity\n"
        with self.assertLogs("forge.packages", level="INFO") as logs:
            self.assertEqual(self._read(listing), {"8086", "10de"})
        self.assertTrue(
            any("1 of 3 display-class line(s) read carry no device identity"
                in line and "(10de, 8086)" in line for line in logs.output),
            logs.output)


class APartialReadingIsRecorded(unittest.TestCase):
    """Some lines of the listing name devices and another cannot be read.

    Measured by the independent read of the previous form of this change,
    2026-09-22: a valid Intel display line beside a malformed display line
    beginning with 10de (a hybrid-graphics shape) gave {"8086"} and wrote
    nothing, so the install record read as a machine whose only display
    device is Intel while the NVIDIA packages were skipped; and the device
    predicate, given a valid line beside a malformed line for the sought card
    reader, answered no and wrote nothing. Both answers are fail-closed and
    are unchanged here; what each consumer now adds, whenever at least one
    line could not be read, is a line counting such lines, named for the field
    that could not be read: the identity or the class here, and the slot in
    the two test classes after this one. Each is written beside valid lines
    as well as alone.
    """

    HYBRID = ("00:02.0 0300: 8086:46a8 (rev 0c)\n"
              "01:00.0 0300: 10de:2484junk (rev a1)\n")

    def setUp(self):
        packages._PCI_VENDOR_CACHE = None
        self.addCleanup(setattr, packages, "_PCI_VENDOR_CACHE", None)

    def _read(self, stdout):
        with mock.patch.object(packages.subprocess, "run",
                               _runner_returning(stdout)):
            return packages.detect_display_pci_vendors()

    def test_the_display_gate_names_the_unreadable_display_line(self):
        with self.assertLogs("forge.packages", level="INFO") as logs:
            self.assertEqual(self._read(self.HYBRID), {"8086"})
        self.assertTrue(
            any("1 of 2 display-class line(s) read carry no device identity"
                in line and "taken from the other 1 (8086)" in line
                for line in logs.output), logs.output)
        self.assertFalse(
            any("listed no PCI devices" in line
                or "none carries a device identity" in line
                for line in logs.output),
            "a listing that named a display vendor was reported as empty: "
            "%s" % logs.output)

    def test_the_line_is_written_once_however_often_the_gate_is_asked(self):
        with self.assertLogs("forge.packages", level="INFO") as logs:
            self.assertEqual(self._read(self.HYBRID), {"8086"})
        self.assertEqual(len(logs.output), 1, logs.output)
        with self.assertNoLogs("forge.packages", level="INFO"):
            self.assertEqual(self._read(self.HYBRID), {"8086"})

    def test_the_predicate_names_it_when_its_answer_is_no(self):
        stdout = ("00:02.0 0300: 8086:46a8 (rev 0c)\n"
                  "2d:00.0 0805: 17a0:9755junk (rev 01)\n")
        with self.assertLogs("forge.packages", level="INFO") as logs:
            answer = packages.target_has_pci_device(
                "17a0", "9755", runner=_runner_returning(stdout))
        self.assertFalse(answer)
        self.assertTrue(
            any("1 of 2 device line(s) read carry no device identity" in line
                and "17a0:9755" in line for line in logs.output), logs.output)
        self.assertFalse(
            any("listed no PCI devices" in line for line in logs.output),
            logs.output)

    def test_the_predicate_names_it_when_its_answer_is_yes(self):
        """The record says the listing was partial whatever the answer."""
        stdout = ("2d:00.0 0805: 17a0:9755 (rev 01)\n"
                  "01:00.0 0300: 10de:2484junk (rev a1)\n")
        with self.assertLogs("forge.packages", level="INFO") as logs:
            answer = packages.target_has_pci_device(
                "17a0", "9755", runner=_runner_returning(stdout))
        self.assertTrue(answer)
        self.assertTrue(
            any("1 of 2 device line(s) read carry no device identity" in line
                for line in logs.output), logs.output)

    def test_a_device_line_whose_class_is_unreadable_is_named(self):
        """A class field that is not a class names no display vendor: taken
        by its first two characters, "03zz" counted as display-class and its
        vendor kept that vendor's gated packages."""
        with self.assertLogs("forge.packages", level="INFO") as logs:
            self.assertEqual(
                self._read("00:02.0 0300: 8086:46a8 (rev 0c)\n"
                           "01:00.0 03zz: 10de:2484 (rev a1)\n"), {"8086"})
        self.assertTrue(
            any("1 device line(s) read carry no readable class" in line
                for line in logs.output), logs.output)

    def test_a_listing_whose_only_line_has_an_unreadable_class_says_both(self):
        with self.assertLogs("forge.packages", level="INFO") as logs:
            self.assertEqual(self._read("01:00.0 03zz: 10de:2484 (rev a1)\n"),
                             set())
        self.assertTrue(any("listed no PCI devices" in line
                            for line in logs.output), logs.output)
        self.assertTrue(
            any("1 device line(s) read carry no readable class" in line
                for line in logs.output), logs.output)

    def test_the_predicate_does_not_take_a_device_from_a_line_without_a_class(
            self):
        """The record names the field that could not be read. The identity on
        this line is well-formed and the class is not; the record once said
        the line carried no device identity, which named the wrong field."""
        stdout = ("00:02.0 0300: 8086:46a8 (rev 0c)\n"
                  "2d:00.0 08zz: 17a0:9755 (rev 01)\n")
        with self.assertLogs("forge.packages", level="INFO") as logs:
            answer = packages.target_has_pci_device(
                "17a0", "9755", runner=_runner_returning(stdout))
        self.assertFalse(answer)
        self.assertTrue(
            any("1 of 2 device line(s) read carry no readable class" in line
                and "17a0:9755" in line for line in logs.output), logs.output)
        self.assertFalse(
            any("no device identity" in line for line in logs.output),
            "a line whose class could not be read was recorded as one "
            "without an identity: %s" % logs.output)

    def test_both_checks_name_an_unreadable_class_as_such_beside_valid_lines(
            self):
        """Four class fields that are not a class, each on a device line
        beside valid lines: 03zz, 08zz on the sought card reader's line, a
        five-digit class and a class without its colon. Before the class rule
        each line was read as a device - the card-reader check answered yes
        for 17a0:9755 on the 08zz line, and the display gate took 10de from
        the three whose field began with 03. None is read now, and both
        checks say how many such lines they read."""
        for listing, vendor, device in (
                (_HOST + _INTEL + "01:00.0 03zz: 10de:2484 (rev a1)\n",
                 "10de", "2484"),
                (_HOST + _INTEL + "2d:00.0 08zz: 17a0:9755 (rev 01)\n",
                 "17a0", "9755"),
                (_HOST + _INTEL + "01:00.0 03000: 10de:2484\n",
                 "10de", "2484"),
                (_HOST + _INTEL + "01:00.0 0300 10de:2484\n",
                 "10de", "2484")):
            with self.subTest(listing=listing):
                packages._PCI_VENDOR_CACHE = None
                with self.assertLogs("forge.packages", level="INFO") as logs:
                    self.assertEqual(self._read(listing), {"8086"})
                self.assertTrue(
                    any("1 device line(s) read carry no readable class" in line
                        for line in logs.output), logs.output)
                with self.assertLogs("forge.packages", level="INFO") as logs:
                    answer = packages.target_has_pci_device(
                        vendor, device, runner=_runner_returning(listing))
                self.assertFalse(answer)
                self.assertTrue(
                    any("1 of 3 device line(s) read carry no readable class"
                        in line for line in logs.output), logs.output)
                self.assertFalse(
                    any("no device identity" in line
                        or "listed no PCI devices" in line
                        for line in logs.output), logs.output)


class ALineIsADeviceLineOnlyWhenItBeginsWithASlot(unittest.TestCase):
    """Position alone is not identity, for the class field either.

    Measured by the independent read of the previous form of this change,
    2026-09-22: a line of prose, "pcilib: 0300 cannot be read", was counted as
    a display-class line because its second word began with 03, and the
    install record described it as one. Every line lspci prints for a device
    begins with the device's slot ("bus:device.function", with the domain in
    front of it on every line once any device has one), so a line that does
    not is not a device line, whatever its later fields hold - for the class
    and for the identity alike.
    """

    def setUp(self):
        packages._PCI_VENDOR_CACHE = None
        self.addCleanup(setattr, packages, "_PCI_VENDOR_CACHE", None)

    def _read(self, stdout):
        with mock.patch.object(packages.subprocess, "run",
                               _runner_returning(stdout)):
            return packages.detect_display_pci_vendors()

    def test_a_line_of_prose_is_not_a_display_class_line(self):
        with self.assertLogs("forge.packages", level="INFO") as logs:
            self.assertEqual(self._read("pcilib: 0300 cannot be read\n"),
                             set())
        self.assertTrue(any("listed no PCI devices" in line
                            for line in logs.output), logs.output)
        self.assertFalse(
            any("display-class line" in line for line in logs.output),
            "a line of prose was counted as a display-class line: "
            "%s" % logs.output)

    def test_a_line_of_prose_beside_a_real_listing_changes_nothing(self):
        """On either check: prose carries no field shaped like an identity,
        so it is not a line that could not be read, and it is not counted."""
        with self.assertNoLogs("forge.packages", level="INFO"):
            self.assertEqual(
                self._read("pcilib: 0300 cannot be read\n" + _INVENTORY),
                {"8086", "10de"})
        with self.assertNoLogs("forge.packages", level="INFO"):
            self.assertTrue(packages.target_has_pci_device(
                "17a0", "9755", runner=_runner_returning(
                    "pcilib: 0300 cannot be read\n" + _INVENTORY)))

    def test_an_identity_on_a_line_without_a_slot_names_no_device(self):
        """The extension of the same rule to the identity: a line that is not
        a device line names no device on either consumer, even when its third
        field has the shape of an identity. Before the slot rule this line
        was read as a device - vendor 10de on the display gate, yes on the
        device check - so each check also says that it read such a line."""
        stdout = "garbage 0300: 10de:2484 (rev a1)\n"
        with self.assertLogs("forge.packages", level="INFO") as logs:
            self.assertEqual(self._read(stdout), set())
        self.assertTrue(any("listed no PCI devices" in line
                            for line in logs.output), logs.output)
        self.assertTrue(
            any("1 line(s) read do not begin with a PCI slot" in line
                and "(1 of them with a display class)" in line
                for line in logs.output), logs.output)
        with self.assertLogs("forge.packages", level="INFO") as logs:
            answer = packages.target_has_pci_device(
                "10de", "2484", runner=_runner_returning(stdout))
        self.assertFalse(answer)
        self.assertTrue(any("listed no PCI devices" in line
                            for line in logs.output), logs.output)
        self.assertTrue(
            any("1 line(s) read do not begin with a PCI slot" in line
                and "(1 of them carry 10de:2484)" in line
                for line in logs.output), logs.output)

    def test_slots_carrying_a_domain_are_read_by_both_consumers(self):
        """lspci prints the domain on every line once any device on the
        machine has a non-zero one, and a domain can be longer than four
        digits (a volume-management controller's 10000)."""
        listing = ("0000:00:02.0 0300: 8086:46a8 (rev 0c)\n"
                   "0000:01:00.0 0300: 10de:24a0 (rev a1)\n"
                   "0000:2d:00.0 0805: 17a0:9755 (rev 01)\n"
                   "10000:e0:17.0 0104: 8086:a77f\n")
        with self.assertNoLogs("forge.packages", level="INFO"):
            self.assertEqual(self._read(listing), {"8086", "10de"})
        with self.assertNoLogs("forge.packages", level="INFO"):
            self.assertTrue(packages.target_has_pci_device(
                "17a0", "9755", runner=_runner_returning(listing)))
            self.assertTrue(packages.target_has_pci_device(
                "8086", "a77f", runner=_runner_returning(listing)))

    def test_every_slot_shape_lspci_prints_is_read_by_both_checks(self):
        """Each line alone: the device check finds its identity and the
        display gate takes the vendor from a display-class line, in silence,
        because each is a true reading."""
        for line, vendor, device, display_vendors in (
                ("00:02.0 0300: 8086:46a8\n", "8086", "46a8", {"8086"}),
                ("2D:00.0 0805: 17A0:9755\n", "17a0", "9755", set()),
                ("00:1f.7 0c05: 8086:51a3\n", "8086", "51a3", set()),
                ("0000:00:02.0 0300: 8086:46a8\n", "8086", "46a8", {"8086"}),
                ("10000:e0:17.0 0104: 8086:a77f\n", "8086", "a77f", set())):
            with self.subTest(line=line):
                packages._PCI_VENDOR_CACHE = None
                with self.assertNoLogs("forge.packages", level="INFO"):
                    self.assertTrue(packages.target_has_pci_device(
                        vendor, device, runner=_runner_returning(line)))
                    self.assertEqual(self._read(line), display_vendors)

    def test_a_line_whose_slot_is_not_lspcis_is_named_alone_and_beside(self):
        """No slot, a one-digit bus, no function, a function above 7 and a
        three-digit domain - and, on a line whose slot is well-formed, a class
        field that is not a class. Before the slot and class rules each of
        these lines was read as Intel's display device 8086:46a8: the display
        gate returned {8086} and the device check answered yes. Now neither
        check reads it, and each says how many such lines it read, named for
        the field that could not be read, whether the line is alone or beside
        a valid one."""
        unslotted = ("pcilib: 0300: 8086:46a8\n", "0:02.0 0300: 8086:46a8\n",
                     "00:02 0300: 8086:46a8\n", "00:02.8 0300: 8086:46a8\n",
                     "000:00:02.0 0300: 8086:46a8\n",
                     "garbage 0300: 8086:46a8\n")
        cases = [(line, "1 line(s) read do not begin with a PCI slot",
                  "(1 of them with a display class)",
                  "(1 of them carry 8086:46a8)") for line in unslotted]
        cases.append(("00:02.0 03zz: 8086:46a8\n",
                      "carry no readable class",
                      "1 device line(s) read carry no readable class",
                      "device line(s) read carry no readable class"))
        for line, both, display_record, device_record in cases:
            for beside in ("", _HOST):
                listing = beside + line
                with self.subTest(listing=listing):
                    packages._PCI_VENDOR_CACHE = None
                    with self.assertLogs("forge.packages",
                                         level="INFO") as display_logs:
                        self.assertEqual(self._read(listing), set())
                    with self.assertLogs("forge.packages",
                                         level="INFO") as device_logs:
                        self.assertFalse(packages.target_has_pci_device(
                            "8086", "46a8",
                            runner=_runner_returning(listing)))
                    for logs, record in ((display_logs, display_record),
                                         (device_logs, device_record)):
                        self.assertTrue(
                            any(both in entry and record in entry
                                for entry in logs.output), logs.output)
                        # "Listed no PCI devices" exactly when nothing
                        # beside the line names a device.
                        self.assertEqual(
                            any("listed no PCI devices" in entry
                                for entry in logs.output), not beside,
                            logs.output)


class ALineTheSlotRuleStopsReadingIsRecordedBesideValidLines(unittest.TestCase):
    """A line the slot rule stops reading is named in the record when valid
    lines stand beside it, not only when it is alone.

    Before the slot rule, a line whose third field had the shape of an
    identity was read as a device whatever its first field held; the rule
    stops reading it. Measured by the independent read of the first form of
    the rule, 2026-09-22: when valid lines stood beside such a line, neither
    check counted it, so answers changed with nothing in the install record.
    A one-digit bus on a discrete NVIDIA card's line beside an Intel display
    line gave {8086} where it had given {10de, 8086}, so the NVIDIA packages
    were skipped and the record read as a machine whose only display device
    is Intel; the card reader at function 8 turned the card-reader check's
    yes into no, so its boot parameter was left out and nothing said why. The
    answers below are the ones the slot rule gives, unchanged; what each
    check adds is one line counting such lines. Prose, which carries no field
    shaped like an identity, is not one of them and stays silent.
    """

    # (listing, the device asked about, the display vendors, how many of the
    # lines without a slot carry a display class). Every such line here
    # carries the device asked about, and the device check answers no.
    BESIDE = (
        # no slot at all, on the sought card reader's line
        (_HOST + _INTEL + _NVIDIA + "garbage 0805: 17a0:9755 (rev 01)\n",
         ("17a0", "9755"), {"10de", "8086"}, 0),
        # no slot, a display class and a vendor no other line names
        (_HOST + _INTEL + "garbage 0300: 1002:73bf\n",
         ("1002", "73bf"), {"8086"}, 1),
        # the card reader at function 8
        (_HOST + _INTEL + _NVIDIA + "2d:00.8 0805: 17a0:9755 (rev 01)\n",
         ("17a0", "9755"), {"10de", "8086"}, 0),
        # a three-digit domain on a display line with a new vendor
        (_HOST + _INTEL + "000:03:00.0 0300: 1002:73bf\n",
         ("1002", "73bf"), {"8086"}, 1),
        # a one-digit bus on the discrete card's line, beside Intel
        (_HOST + _INTEL + "1:00.0 0300: 10de:2484 (rev a1)\n",
         ("10de", "2484"), {"8086"}, 1),
    )

    def setUp(self):
        packages._PCI_VENDOR_CACHE = None
        self.addCleanup(setattr, packages, "_PCI_VENDOR_CACHE", None)

    def _read(self, stdout):
        packages._PCI_VENDOR_CACHE = None
        with mock.patch.object(packages.subprocess, "run",
                               _runner_returning(stdout)):
            return packages.detect_display_pci_vendors()

    def test_the_display_gate_counts_it(self):
        for listing, _device, display_vendors, with_class in self.BESIDE:
            with self.subTest(listing=listing):
                with self.assertLogs("forge.packages", level="INFO") as logs:
                    self.assertEqual(self._read(listing), display_vendors)
                self.assertTrue(
                    any("1 line(s) read do not begin with a PCI slot" in line
                        and "(%d of them with a display class)" % with_class
                        in line for line in logs.output), logs.output)
                self.assertFalse(
                    any("listed no PCI devices" in line
                        for line in logs.output), logs.output)

    def test_the_card_reader_check_counts_it(self):
        for listing, (vendor, device), _vendors, _class in self.BESIDE:
            with self.subTest(listing=listing):
                want = "%s:%s" % (vendor, device)
                with self.assertLogs("forge.packages", level="INFO") as logs:
                    answer = packages.target_has_pci_device(
                        vendor, device, runner=_runner_returning(listing))
                self.assertFalse(answer)
                self.assertTrue(
                    any("1 line(s) read do not begin with a PCI slot" in line
                        and "(1 of them carry %s)" % want in line
                        for line in logs.output), logs.output)
                self.assertFalse(
                    any("listed no PCI devices" in line
                        for line in logs.output), logs.output)

    def test_prose_beside_a_valid_listing_stays_silent_on_both_checks(self):
        listing = "pcilib: 0300 cannot be read\n" + _HOST + _INTEL + _NVIDIA
        with self.assertNoLogs("forge.packages", level="INFO"):
            self.assertEqual(self._read(listing), {"10de", "8086"})
            self.assertTrue(packages.target_has_pci_device(
                "10de", "2484", runner=_runner_returning(listing)))


if __name__ == "__main__":
    unittest.main()
