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
import unittest
from unittest import mock

from installer.tests import class2b_boot_order as c2b


# --- Fixtures ---------------------------------------------------------------

# A representative efibootmgr -v output. Real efibootmgr separates the
# label from the device-path with at least two spaces (or a tab); we use
# spaces here because that's what the upstream implementation emits on
# Ubuntu / Fedora / Arch.
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
            report = c2b.run("InterGenOS")
        self.assertTrue(report.all_required_pass())
        # 3 probes expected: entry-exists, entry-in-boot-order, boot-current
        self.assertEqual(len(report.results), 3)

    def test_missing_intergenos_entry_fails_overall(self):
        with mock.patch(
            "installer.tests.class2b_boot_order.shutil.which",
            return_value="/usr/bin/efibootmgr",
        ), mock.patch(
            "installer.tests.class2b_boot_order.subprocess.run",
            _mock_efibootmgr(EFIBOOTMGR_NO_INTERGENOS),
        ):
            report = c2b.run("InterGenOS")
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
            report = c2b.run("InterGenOS")
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
            report = c2b.run("InterGenOS")
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
            report = c2b.run("InterGenOS")
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
            report = c2b.run("InterGenOS")
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


if __name__ == "__main__":
    unittest.main()
