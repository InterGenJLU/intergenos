"""Unit tests for class2_runtime_sb_state — mocked efivars + mocked mokutil.

Runs on any host: no efivars, no mokutil, no root required. Uses a temp
dir as the efivars root and writes EFI-variable-shaped files into it.

Integration-style coverage (running the probes against a REAL installed
InterGenOS target) will live in a separate TestClass2PostReboot class
once a post-install target exists — same pattern as Class 1's
TestClass1PostInstall split.
"""

from __future__ import annotations

import contextlib
import io
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.tests import class2_runtime_sb_state as c2


# EFI_VARIABLE_NON_VOLATILE | BOOTSERVICE_ACCESS | RUNTIME_ACCESS (common)
ATTR_HEADER = struct.pack("<I", 0x07)


def _write_efivar(efivars_dir: Path, name: str, byte_value: int) -> Path:
    """Write a mock EFI variable file at efivars_dir/<name>-<GUID>."""
    path = efivars_dir / f"{name}-{c2.EFI_GLOBAL_GUID}"
    path.write_bytes(ATTR_HEADER + bytes([byte_value]))
    return path


class TestReadEfivarByte(unittest.TestCase):
    def test_missing_variable(self):
        with tempfile.TemporaryDirectory() as td:
            value, detail = c2._read_efivar_byte(Path(td), "SecureBoot")
        self.assertIsNone(value)
        self.assertIn("not present", detail)

    def test_value_1(self):
        with tempfile.TemporaryDirectory() as td:
            _write_efivar(Path(td), "SecureBoot", 1)
            value, detail = c2._read_efivar_byte(Path(td), "SecureBoot")
        self.assertEqual(value, 1)
        self.assertEqual(detail, "")

    def test_value_0(self):
        with tempfile.TemporaryDirectory() as td:
            _write_efivar(Path(td), "SecureBoot", 0)
            value, _ = c2._read_efivar_byte(Path(td), "SecureBoot")
        self.assertEqual(value, 0)

    def test_truncated_variable(self):
        """Variable file shorter than the 4-byte attribute header + 1 payload byte."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / f"SecureBoot-{c2.EFI_GLOBAL_GUID}"
            # Just 3 bytes — shorter than the required 5
            path.write_bytes(b"\x00\x00\x00")
            value, detail = c2._read_efivar_byte(Path(td), "SecureBoot")
        self.assertIsNone(value)
        self.assertIn("truncated", detail)


class TestProbeSecureboot(unittest.TestCase):
    def test_not_present(self):
        with tempfile.TemporaryDirectory() as td:
            r = c2.probe_secureboot(Path(td))
        self.assertFalse(r.passed)
        self.assertIsNone(r.observed)
        self.assertTrue(r.required)

    def test_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            _write_efivar(Path(td), "SecureBoot", 1)
            r = c2.probe_secureboot(Path(td))
        self.assertTrue(r.passed)
        self.assertEqual(r.observed, "1")

    def test_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            _write_efivar(Path(td), "SecureBoot", 0)
            r = c2.probe_secureboot(Path(td))
        self.assertFalse(r.passed)
        self.assertEqual(r.observed, "0")
        self.assertIn("not enabled", r.detail)


class TestProbeSetupMode(unittest.TestCase):
    def test_not_present(self):
        with tempfile.TemporaryDirectory() as td:
            r = c2.probe_setupmode(Path(td))
        self.assertFalse(r.passed)
        self.assertIsNone(r.observed)
        self.assertTrue(r.required)

    def test_user_mode(self):
        """SetupMode=0 is the locked-down posture we want."""
        with tempfile.TemporaryDirectory() as td:
            _write_efivar(Path(td), "SetupMode", 0)
            r = c2.probe_setupmode(Path(td))
        self.assertTrue(r.passed)
        self.assertEqual(r.observed, "0")

    def test_setup_mode_flagged(self):
        """SetupMode=1 means PK is not enrolled — SB is spoofable."""
        with tempfile.TemporaryDirectory() as td:
            _write_efivar(Path(td), "SetupMode", 1)
            r = c2.probe_setupmode(Path(td))
        self.assertFalse(r.passed)
        self.assertEqual(r.observed, "1")
        self.assertIn("Setup Mode", r.detail)


class TestProbeMokutil(unittest.TestCase):
    def test_mokutil_absent(self):
        """Not-in-PATH is a CLEAN skip, not a failure (it's supplementary)."""
        with mock.patch("installer.tests.class2_runtime_sb_state.shutil.which",
                        return_value=None):
            r = c2.probe_mokutil()
        self.assertTrue(r.passed)  # not required, so skipped-passed
        self.assertFalse(r.required)
        self.assertIn("not installed", r.detail)

    def test_mokutil_reports_enabled(self):
        fake_run = mock.MagicMock()
        fake_run.return_value.stdout = "SecureBoot enabled\n"
        fake_run.return_value.stderr = ""
        fake_run.return_value.returncode = 0
        with mock.patch("installer.tests.class2_runtime_sb_state.shutil.which",
                        return_value="/usr/bin/mokutil"), \
             mock.patch("installer.tests.class2_runtime_sb_state.subprocess.run",
                        fake_run):
            r = c2.probe_mokutil()
        self.assertTrue(r.passed)
        self.assertEqual(r.observed, "enabled")

    def test_mokutil_reports_disabled(self):
        fake_run = mock.MagicMock()
        fake_run.return_value.stdout = "SecureBoot disabled\n"
        fake_run.return_value.stderr = ""
        fake_run.return_value.returncode = 0
        with mock.patch("installer.tests.class2_runtime_sb_state.shutil.which",
                        return_value="/usr/bin/mokutil"), \
             mock.patch("installer.tests.class2_runtime_sb_state.subprocess.run",
                        fake_run):
            r = c2.probe_mokutil()
        self.assertFalse(r.passed)
        self.assertEqual(r.observed, "disabled")
        self.assertIn("disabled", r.detail)

    def test_mokutil_output_unparseable(self):
        fake_run = mock.MagicMock()
        fake_run.return_value.stdout = "unexpected garbage"
        fake_run.return_value.stderr = ""
        fake_run.return_value.returncode = 0
        with mock.patch("installer.tests.class2_runtime_sb_state.shutil.which",
                        return_value="/usr/bin/mokutil"), \
             mock.patch("installer.tests.class2_runtime_sb_state.subprocess.run",
                        fake_run):
            r = c2.probe_mokutil()
        self.assertFalse(r.passed)
        self.assertIsNone(r.observed)
        self.assertIn("unparseable", r.detail)


class TestRun(unittest.TestCase):
    def test_all_required_pass_with_mokutil_absent(self):
        """SB=1 + SetupMode=0 + mokutil absent -> overall PASS."""
        with tempfile.TemporaryDirectory() as td:
            _write_efivar(Path(td), "SecureBoot", 1)
            _write_efivar(Path(td), "SetupMode", 0)
            with mock.patch(
                "installer.tests.class2_runtime_sb_state.shutil.which",
                return_value=None,
            ):
                report = c2.run(Path(td))
        self.assertTrue(report.all_required_pass())
        self.assertEqual(len(report.results), 3)

    def test_setupmode_failure_fails_overall(self):
        """SB=1 but SetupMode=1 -> FAIL (SB is nominally on but spoofable)."""
        with tempfile.TemporaryDirectory() as td:
            _write_efivar(Path(td), "SecureBoot", 1)
            _write_efivar(Path(td), "SetupMode", 1)
            with mock.patch(
                "installer.tests.class2_runtime_sb_state.shutil.which",
                return_value=None,
            ):
                report = c2.run(Path(td))
        self.assertFalse(report.all_required_pass())

    def test_json_report_shape(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            _write_efivar(Path(td), "SecureBoot", 1)
            _write_efivar(Path(td), "SetupMode", 0)
            with mock.patch(
                "installer.tests.class2_runtime_sb_state.shutil.which",
                return_value=None,
            ):
                report = c2.run(Path(td))
            d = report.to_dict()
        reloaded = json.loads(json.dumps(d))
        self.assertTrue(reloaded["all_required_pass"])
        self.assertEqual(len(reloaded["results"]), 3)
        for r in reloaded["results"]:
            self.assertIn(r["probe"], {"secureboot", "setupmode", "mokutil"})


class TestCLI(unittest.TestCase):
    """Smoke-check the CLI wrapping on a real filesystem invocation.

    stdout is redirected to /dev/null equivalents so CLI print() output
    doesn't pollute the test runner.
    """

    def test_cli_exits_nonzero_when_required_probes_fail(self):
        """Missing SB + SetupMode vars -> exit 1 (required failures)."""
        with tempfile.TemporaryDirectory() as td:
            with mock.patch(
                "installer.tests.class2_runtime_sb_state.shutil.which",
                return_value=None,
            ), contextlib.redirect_stdout(io.StringIO()):
                rc = c2.main(["--efivars-dir", td, "--json"])
        self.assertEqual(rc, 1)

    def test_report_only_returns_zero_on_fail(self):
        """--report-only means exit 0 regardless of pass/fail."""
        with tempfile.TemporaryDirectory() as td:
            with mock.patch(
                "installer.tests.class2_runtime_sb_state.shutil.which",
                return_value=None,
            ), contextlib.redirect_stdout(io.StringIO()):
                rc = c2.main([
                    "--efivars-dir", td, "--json", "--report-only",
                ])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
