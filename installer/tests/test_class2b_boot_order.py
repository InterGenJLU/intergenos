# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Unit tests for class2b_boot_order — mocked efibootmgr.

Runs on any host: no efivars, no efibootmgr, no root required. All
subprocess calls mocked.

Integration-style coverage (running against a REAL UEFI target with
efibootmgr installed) is left for a future TestClass2bPostReboot sibling
class, same pattern as Class 1's post-install split.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.tests import class2b_boot_order as c2b


@contextlib.contextmanager
def _intent_file(default_boot_target: bool):
    """A throwaway copy of the installer's recorded default-boot-target file.

    The real one lives at /etc/intergenos/boot-default.conf and exists only on
    an installed machine. Writing our own means a case can exercise BOTH
    answers — the install recorded InterGenOS as the default, or it recorded
    another operating system — on any host, and neither answer can be supplied
    accidentally by the machine running the suite.
    """
    value = "yes" if default_boot_target else "no"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "boot-default.conf"
        path.write_text(
            "# throwaway fixture, shaped as the installer writes it\n"
            f"default_boot_target={value}\n"
            "boot_entry_label=InterGenOS\n"
        )
        yield str(path)


# --- Fixtures ---------------------------------------------------------------

# A representative efibootmgr -v output. Real efibootmgr separates the
# label from the device-path with at least two spaces (or a tab); we use
# spaces here because that's what the upstream implementation emits on
# Ubuntu / Fedora / Arch.
# A path that cannot exist, so run() finds no recorded default-boot-target
# intent and the entry-is-first probe reports instead of asserting.
_NO_INTENT_FILE = "/nonexistent/intergenos/boot-default.conf"

EFIBOOTMGR_GOOD = """\
BootCurrent: 0001
Timeout: 0 seconds
BootOrder: 0001,0000,0002
Boot0000* InterGenOS  HD(1,GPT,abc,0x800,0x12345)/File(\\EFI\\InterGenOS\\shimx64.efi)
Boot0001* Ubuntu  HD(1,GPT,def,0x800,0x54321)/File(\\EFI\\ubuntu\\shimx64.efi)
Boot0002* UEFI Internal Disk  ACPI(a0341d0,0)/Pci(1f|2)/Sata(0,0,0)
"""

EFIBOOTMGR_NO_INTERGENOS = """\
BootCurrent: 0001
BootOrder: 0001,0002
Boot0001* Ubuntu  HD(1,GPT,def,0x800,0x54321)/File(\\EFI\\ubuntu\\shimx64.efi)
Boot0002* UEFI Internal Disk  ACPI(a0341d0,0)/Pci(1f|2)/Sata(0,0,0)
"""

EFIBOOTMGR_ENTRY_NOT_IN_ORDER = """\
BootCurrent: 0001
BootOrder: 0001,0002
Boot0000* InterGenOS  HD(1,GPT,abc,0x800,0x12345)/File(\\EFI\\InterGenOS\\shimx64.efi)
Boot0001* Ubuntu  HD(1,GPT,def,0x800,0x54321)/File(\\EFI\\ubuntu\\shimx64.efi)
Boot0002* UEFI Internal Disk  ACPI(a0341d0,0)/Pci(1f|2)/Sata(0,0,0)
"""

EFIBOOTMGR_INACTIVE_ENTRY = """\
BootCurrent: 0001
BootOrder: 0001,0000
Boot0000  InterGenOS  HD(1,GPT,abc,0x800,0x12345)/File(\\EFI\\InterGenOS\\shimx64.efi)
Boot0001* Ubuntu  HD(1,GPT,def,0x800,0x54321)/File(\\EFI\\ubuntu\\shimx64.efi)
"""

EFIBOOTMGR_DUPLICATE_ENTRIES = """\
BootCurrent: 0001
BootOrder: 0001,0000,0003
Boot0000* InterGenOS  HD(1,GPT,abc,0x800,0x12345)/File(\\EFI\\InterGenOS\\shimx64.efi)
Boot0001* Ubuntu  HD(1,GPT,def,0x800,0x54321)/File(\\EFI\\ubuntu\\shimx64.efi)
Boot0003* InterGenOS  HD(2,GPT,ghi,0x800,0x12345)/File(\\EFI\\InterGenOS\\shimx64.efi)
"""


def _mock_efibootmgr(stdout: str, returncode: int = 0, stderr: str = ""):
    """Return a mock for subprocess.run that emits the fixture output."""
    fake = mock.MagicMock()
    fake.return_value.stdout = stdout
    fake.return_value.stderr = stderr
    fake.return_value.returncode = returncode
    return fake


# --- Parse tests -----------------------------------------------------------


class TestParseEfibootmgr(unittest.TestCase):
    def test_good_output(self):
        entries, order, current = c2b._parse_efibootmgr(EFIBOOTMGR_GOOD)
        self.assertEqual(len(entries), 3)
        self.assertEqual(order, ["0001", "0000", "0002"])
        self.assertEqual(current, "0001")
        labels = [e.label for e in entries]
        self.assertIn("InterGenOS", labels)
        self.assertIn("Ubuntu", labels)

    def test_entries_have_active_flag(self):
        entries, _, _ = c2b._parse_efibootmgr(EFIBOOTMGR_INACTIVE_ENTRY)
        by_id = {e.id: e for e in entries}
        self.assertFalse(by_id["0000"].active)
        self.assertTrue(by_id["0001"].active)

    def test_empty_output(self):
        entries, order, current = c2b._parse_efibootmgr("")
        self.assertEqual(entries, [])
        self.assertEqual(order, [])
        self.assertIsNone(current)

    def test_case_normalization(self):
        """Hex IDs lowercase in source, uppercased in parsed output."""
        text = (
            "BootCurrent: 00aa\n"
            "BootOrder: 00aa,00bb\n"
            "Boot00aa* InterGenOS  HD(...)/File(...)\n"
        )
        entries, order, current = c2b._parse_efibootmgr(text)
        self.assertEqual(current, "00AA")
        self.assertEqual(order, ["00AA", "00BB"])
        self.assertEqual(entries[0].id, "00AA")


# --- Probe tests -----------------------------------------------------------


class TestProbeEntryExists(unittest.TestCase):
    def test_entry_present(self):
        entries, _, _ = c2b._parse_efibootmgr(EFIBOOTMGR_GOOD)
        r = c2b.probe_entry_exists(entries, "InterGenOS")
        self.assertTrue(r.passed)
        self.assertEqual(r.observed, "0000")
        self.assertEqual(r.detail, "")

    def test_entry_absent(self):
        entries, _, _ = c2b._parse_efibootmgr(EFIBOOTMGR_NO_INTERGENOS)
        r = c2b.probe_entry_exists(entries, "InterGenOS")
        self.assertFalse(r.passed)
        self.assertIn("no Boot#### entry", r.detail)

    def test_duplicate_entries_pass_with_note(self):
        entries, _, _ = c2b._parse_efibootmgr(EFIBOOTMGR_DUPLICATE_ENTRIES)
        r = c2b.probe_entry_exists(entries, "InterGenOS")
        self.assertTrue(r.passed)
        self.assertIn("multiple entries", r.detail)
        self.assertIn("0000", r.observed)
        self.assertIn("0003", r.observed)


class TestProbeEntryInBootOrder(unittest.TestCase):
    def test_entry_in_order(self):
        entries, order, _ = c2b._parse_efibootmgr(EFIBOOTMGR_GOOD)
        r = c2b.probe_entry_in_boot_order(entries, order, "InterGenOS")
        self.assertTrue(r.passed)
        self.assertIn("position 1", r.observed)

    def test_entry_exists_but_not_in_order(self):
        """Entry present but not referenced by BootOrder — firmware skips it."""
        entries, order, _ = c2b._parse_efibootmgr(EFIBOOTMGR_ENTRY_NOT_IN_ORDER)
        r = c2b.probe_entry_in_boot_order(entries, order, "InterGenOS")
        self.assertFalse(r.passed)
        self.assertIn("none appear in BootOrder", r.detail)

    def test_entry_missing_altogether(self):
        entries, order, _ = c2b._parse_efibootmgr(EFIBOOTMGR_NO_INTERGENOS)
        r = c2b.probe_entry_in_boot_order(entries, order, "InterGenOS")
        self.assertFalse(r.passed)
        self.assertIn("see entry-exists", r.detail)

    def test_empty_boot_order_fails(self):
        entries, _, _ = c2b._parse_efibootmgr(EFIBOOTMGR_GOOD)
        r = c2b.probe_entry_in_boot_order(entries, [], "InterGenOS")
        self.assertFalse(r.passed)
        self.assertIn("BootOrder", r.detail)


class TestProbeBootCurrent(unittest.TestCase):
    def test_boot_current_matches(self):
        entries, _, _ = c2b._parse_efibootmgr(EFIBOOTMGR_GOOD)
        # BootCurrent in EFIBOOTMGR_GOOD is 0001 (Ubuntu). Force a match
        # scenario by pretending BootCurrent points at InterGenOS.
        r = c2b.probe_boot_current_is_label(entries, "0000", "InterGenOS")
        self.assertTrue(r.passed)
        self.assertEqual(r.observed, "0000")

    def test_boot_current_missing(self):
        entries, _, _ = c2b._parse_efibootmgr(EFIBOOTMGR_GOOD)
        r = c2b.probe_boot_current_is_label(entries, None, "InterGenOS")
        self.assertTrue(r.passed)  # supplementary, not-required -> skip-pass
        self.assertFalse(r.required)
        self.assertIn("not reported", r.detail)

    def test_boot_current_mismatch_does_not_fail_overall(self):
        """Supplementary probe: failure does NOT mark overall report FAIL."""
        entries, _, _ = c2b._parse_efibootmgr(EFIBOOTMGR_GOOD)
        r = c2b.probe_boot_current_is_label(entries, "0001", "InterGenOS")
        self.assertFalse(r.passed)
        self.assertFalse(r.required)
        self.assertIn("different boot entry", r.detail)


# --- End-to-end run tests ---------------------------------------------------


class TestRun(unittest.TestCase):
    def test_good_path_all_required_pass(self):
        with mock.patch(
            "installer.tests.class2b_boot_order.shutil.which",
            return_value="/usr/bin/efibootmgr",
        ), mock.patch(
            "installer.tests.class2b_boot_order.subprocess.run",
            _mock_efibootmgr(EFIBOOTMGR_GOOD),
        ):
            # Explicit non-existent intent path: these fixtures must behave
            # the same whether or not the machine running the suite is itself
            # an installed InterGenOS system carrying a real record.
            report = c2b.run("InterGenOS", _NO_INTENT_FILE)
        self.assertTrue(report.all_required_pass())
        # 4 probes expected: entry-exists, entry-in-boot-order, entry-is-first,
        # boot-current.
        self.assertEqual(len(report.results), 4)
        self.assertEqual([r.probe for r in report.results],
                         ["entry-exists", "entry-in-boot-order",
                          "entry-is-first", "boot-current"])
        # This fixture has Ubuntu first and InterGenOS second. With no
        # recorded install intent that is reported, not judged.
        first = [r for r in report.results if r.probe == "entry-is-first"][0]
        self.assertFalse(first.required)

    def test_missing_intergenos_entry_fails_overall(self):
        with mock.patch(
            "installer.tests.class2b_boot_order.shutil.which",
            return_value="/usr/bin/efibootmgr",
        ), mock.patch(
            "installer.tests.class2b_boot_order.subprocess.run",
            _mock_efibootmgr(EFIBOOTMGR_NO_INTERGENOS),
        ):
            # Explicit non-existent intent path: these fixtures must behave
            # the same whether or not the machine running the suite is itself
            # an installed InterGenOS system carrying a real record.
            report = c2b.run("InterGenOS", _NO_INTENT_FILE)
        self.assertFalse(report.all_required_pass())
        # All three probes should have landed; at least entry-exists + order must fail
        passed = [r for r in report.results if r.passed]
        self.assertLess(len(passed), 3)

    def test_entry_present_but_not_in_boot_order_fails(self):
        with mock.patch(
            "installer.tests.class2b_boot_order.shutil.which",
            return_value="/usr/bin/efibootmgr",
        ), mock.patch(
            "installer.tests.class2b_boot_order.subprocess.run",
            _mock_efibootmgr(EFIBOOTMGR_ENTRY_NOT_IN_ORDER),
        ):
            # Explicit non-existent intent path: these fixtures must behave
            # the same whether or not the machine running the suite is itself
            # an installed InterGenOS system carrying a real record.
            report = c2b.run("InterGenOS", _NO_INTENT_FILE)
        self.assertFalse(report.all_required_pass())
        exists = next(r for r in report.results if r.probe == "entry-exists")
        in_order = next(
            r for r in report.results if r.probe == "entry-in-boot-order"
        )
        self.assertTrue(exists.passed)
        self.assertFalse(in_order.passed)

    def test_efibootmgr_missing_from_path(self):
        with mock.patch(
            "installer.tests.class2b_boot_order.shutil.which",
            return_value=None,
        ):
            # Explicit non-existent intent path: these fixtures must behave
            # the same whether or not the machine running the suite is itself
            # an installed InterGenOS system carrying a real record.
            report = c2b.run("InterGenOS", _NO_INTENT_FILE)
        self.assertFalse(report.all_required_pass())
        # One synthetic "efibootmgr-read" probe with the missing-tool detail
        self.assertEqual(len(report.results), 1)
        self.assertEqual(report.results[0].probe, "efibootmgr-read")
        self.assertIn("not in PATH", report.results[0].detail)

    def test_efibootmgr_returns_nonzero(self):
        with mock.patch(
            "installer.tests.class2b_boot_order.shutil.which",
            return_value="/usr/bin/efibootmgr",
        ), mock.patch(
            "installer.tests.class2b_boot_order.subprocess.run",
            _mock_efibootmgr(
                "", returncode=1, stderr="EFI variables not supported"
            ),
        ):
            # Explicit non-existent intent path: these fixtures must behave
            # the same whether or not the machine running the suite is itself
            # an installed InterGenOS system carrying a real record.
            report = c2b.run("InterGenOS", _NO_INTENT_FILE)
        self.assertFalse(report.all_required_pass())
        read_result = report.results[0]
        self.assertEqual(read_result.probe, "efibootmgr-read")
        self.assertIn("failed", read_result.detail)

    def test_json_report_shape(self):
        import json as _json
        with mock.patch(
            "installer.tests.class2b_boot_order.shutil.which",
            return_value="/usr/bin/efibootmgr",
        ), mock.patch(
            "installer.tests.class2b_boot_order.subprocess.run",
            _mock_efibootmgr(EFIBOOTMGR_GOOD),
        ):
            # Explicit non-existent intent path: these fixtures must behave
            # the same whether or not the machine running the suite is itself
            # an installed InterGenOS system carrying a real record.
            report = c2b.run("InterGenOS", _NO_INTENT_FILE)
            d = report.to_dict()
        reloaded = _json.loads(_json.dumps(d))
        self.assertTrue(reloaded["all_required_pass"])
        self.assertEqual(reloaded["label"], "InterGenOS")
        self.assertEqual(reloaded["boot_current"], "0001")
        self.assertEqual(len(reloaded["entries"]), 3)


# --- CLI smoke -------------------------------------------------------------


class TestCLI(unittest.TestCase):
    """stdout redirected so CLI print() doesn't pollute test runner output."""

    def test_cli_good_path_exits_zero(self):
        with mock.patch(
            "installer.tests.class2b_boot_order.shutil.which",
            return_value="/usr/bin/efibootmgr",
        ), mock.patch(
            "installer.tests.class2b_boot_order.subprocess.run",
            _mock_efibootmgr(EFIBOOTMGR_GOOD),
        ), contextlib.redirect_stdout(io.StringIO()):
            rc = c2b.main(["--label", "InterGenOS", "--json"])
        self.assertEqual(rc, 0)

    def test_cli_missing_entry_exits_nonzero(self):
        with mock.patch(
            "installer.tests.class2b_boot_order.shutil.which",
            return_value="/usr/bin/efibootmgr",
        ), mock.patch(
            "installer.tests.class2b_boot_order.subprocess.run",
            _mock_efibootmgr(EFIBOOTMGR_NO_INTERGENOS),
        ), contextlib.redirect_stdout(io.StringIO()):
            rc = c2b.main(["--label", "InterGenOS", "--json"])
        self.assertEqual(rc, 1)

    def test_cli_report_only_returns_zero_even_on_fail(self):
        with mock.patch(
            "installer.tests.class2b_boot_order.shutil.which",
            return_value=None,
        ), contextlib.redirect_stdout(io.StringIO()):
            rc = c2b.main([
                "--label", "InterGenOS", "--json", "--report-only",
            ])
        self.assertEqual(rc, 0)


class TestTheCLITestsDoNotReadTheHostMachine(unittest.TestCase):
    """The CLI cases must answer from their fixtures alone.

    `main()` defaults --intent-file to /etc/intergenos/boot-default.conf, which
    is a REAL FILE on an installed InterGenOS machine and absent on a
    development box. That file decides whether the entry-is-first probe is
    required, so the CLI cases above were answering a different question
    depending on where the suite ran: on a development box no intent was found
    and the probe only reported, while on an installed machine the intent said
    InterGenOS is the default, the probe became required, and the fixture —
    whose BootOrder starts with another operating system — correctly failed it.

    Every case in TestRun already pins the path (_NO_INTENT_FILE) with a
    comment saying exactly why. The CLI cases did not, and that is the defect
    these tests pin. A test whose result depends on the machine running it is
    not a test.
    """

    def _rc(self, output, argv_extra):
        with mock.patch(
            "installer.tests.class2b_boot_order.shutil.which",
            return_value="/usr/bin/efibootmgr",
        ), mock.patch(
            "installer.tests.class2b_boot_order.subprocess.run",
            _mock_efibootmgr(output),
        ), contextlib.redirect_stdout(io.StringIO()):
            return c2b.main(["--label", "InterGenOS", "--json"] + argv_extra)

    def test_every_cli_case_pins_the_intent_file(self):
        """Read from the source, because the point is that no case may fall
        back to the default path — a case that happens to pass today on the
        machine at hand would still be reading the host."""
        source = Path(__file__).read_text()
        body = source[source.index("class TestCLI("):
                      source.index("class TestTheCLITestsDoNotReadTheHostMachine(")]
        calls = body.count("c2b.main(")
        pinned = body.count("--intent-file")
        self.assertGreater(calls, 0)
        self.assertEqual(
            calls, pinned,
            f"{calls - pinned} of {calls} CLI case(s) let --intent-file fall "
            "back to /etc/intergenos/boot-default.conf, so their result "
            "depends on whether the machine running the suite is an installed "
            "InterGenOS system")

    def test_a_healthy_machine_fixture_exists(self):
        """There was no fixture for a machine that is actually in the state
        the installer aims for — InterGenOS first in BootOrder and booted.
        Without one, nothing could assert the good path end to end."""
        entries, order, current = c2b._parse_efibootmgr(EFIBOOTMGR_INTERGENOS_FIRST)
        intergenos = [e for e in entries if e.label == "InterGenOS"]
        self.assertEqual(len(intergenos), 1)
        self.assertEqual(order[0], intergenos[0].id,
                         "the healthy fixture does not put InterGenOS first")
        self.assertEqual(current, intergenos[0].id,
                         "the healthy fixture is not booted from InterGenOS")

    def test_the_healthy_machine_passes_even_when_it_is_the_recorded_default(self):
        """The strictest case: the install recorded InterGenOS as the default,
        so entry-is-first is REQUIRED — and a healthy machine still passes."""
        with _intent_file(default_boot_target=True) as path:
            self.assertEqual(
                self._rc(EFIBOOTMGR_INTERGENOS_FIRST, ["--intent-file", path]), 0)

    def test_a_demoted_entry_fails_when_intergenos_is_the_recorded_default(self):
        """A positive control. This is the condition measured on a real
        installation on 2026-08-24: the entry exists, it is in BootOrder, the
        install recorded it as the default — and the firmware boots something
        else. An instrument that never fails here could not have detected it.
        """
        with _intent_file(default_boot_target=True) as path:
            self.assertEqual(
                self._rc(EFIBOOTMGR_GOOD, ["--intent-file", path]), 1)

    def test_a_demoted_entry_is_reported_not_judged_without_intent(self):
        """Same firmware state, no recorded intent: nothing is asserted, so
        the exit status is zero. The two cases above and this one differ ONLY
        in the intent file, which is what makes reading the host's copy a
        defect rather than a detail."""
        self.assertEqual(
            self._rc(EFIBOOTMGR_GOOD,
                     ["--intent-file", _NO_INTENT_FILE]), 0)

    def test_a_demoted_entry_passes_when_another_os_is_the_recorded_default(self):
        """The user kept another operating system first and the install
        recorded that. Being second is then correct, not a fault."""
        with _intent_file(default_boot_target=False) as path:
            self.assertEqual(
                self._rc(EFIBOOTMGR_GOOD, ["--intent-file", path]), 0)


if __name__ == "__main__":
    unittest.main()
