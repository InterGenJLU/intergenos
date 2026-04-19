"""Unit tests for installer.tests.class1_chain_verify.

Scope: validate the script's logic (path probing, sbverify output parsing,
report aggregation, exit codes) without requiring sbverify, sbsign, or real
PE/COFF binaries on the host. Subprocess calls are mocked; artifacts are
empty files in tempdirs.

Day 2 work will add integration tests that exercise real sbsign/sbverify
against real binaries in a SecBoot-enabled VM.

Run:
    python3 -m installer.tests.test_class1_chain_verify
or
    python3 -m unittest installer.tests.test_class1_chain_verify
"""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from installer.tests import class1_chain_verify as c1


def _fake_completed(returncode: int, stdout: str = "", stderr: str = ""):
    """Build a minimal CompletedProcess-like object for mocking."""
    cp = MagicMock()
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


class TestSbverify(unittest.TestCase):
    """Tests for c1._sbverify() — subprocess wrapper + output parsing."""

    def setUp(self):
        # Real paths don't matter; sbverify is mocked
        self.binary = Path("/fake/binary.efi")
        self.cert = Path("/fake/cert.crt")

    def test_sbverify_success(self):
        with patch("subprocess.run", return_value=_fake_completed(0)):
            sig_present, verified, detail = c1._sbverify(self.binary, self.cert)
        self.assertTrue(sig_present)
        self.assertTrue(verified)
        self.assertEqual(detail, "")

    def test_sbverify_no_signature(self):
        with patch("subprocess.run", return_value=_fake_completed(
            1, stderr="warning: no signature table present"
        )):
            sig_present, verified, detail = c1._sbverify(self.binary, self.cert)
        self.assertFalse(sig_present)
        self.assertFalse(verified)
        self.assertIn("no signature", detail)

    def test_sbverify_bad_signature(self):
        with patch("subprocess.run", return_value=_fake_completed(
            1, stderr="Signature verification failed: wrong issuer"
        )):
            sig_present, verified, detail = c1._sbverify(self.binary, self.cert)
        # Bad sig = sig block present, but cert mismatch
        self.assertTrue(sig_present)
        self.assertFalse(verified)
        self.assertIn("wrong issuer", detail)

    def test_sbverify_tool_missing(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            sig_present, verified, detail = c1._sbverify(self.binary, self.cert)
        self.assertFalse(sig_present)
        self.assertFalse(verified)
        self.assertIn("not installed", detail)

    def test_sbverify_timeout(self):
        with patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="sbverify", timeout=10)):
            sig_present, verified, detail = c1._sbverify(self.binary, self.cert)
        self.assertFalse(sig_present)
        self.assertFalse(verified)
        self.assertIn("timeout", detail)


class TestProbeFirstExisting(unittest.TestCase):
    """Tests for c1._probe_first_existing() — FAT32 case-probing helper."""

    def test_returns_first_existing(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            # Create only the second candidate; confirm probe picks it
            (tdp / "foo").mkdir()
            (tdp / "foo" / "bar.efi").write_bytes(b"")
            result = c1._probe_first_existing(tdp, [
                "missing/file.efi",
                "foo/bar.efi",
            ])
        self.assertEqual(result, tdp / "foo" / "bar.efi")

    def test_returns_none_when_nothing_matches(self):
        with tempfile.TemporaryDirectory() as td:
            result = c1._probe_first_existing(Path(td), [
                "no.efi", "still_no.efi",
            ])
        self.assertIsNone(result)

    def test_picks_first_when_both_exist(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "a").mkdir()
            (tdp / "b").mkdir()
            (tdp / "a" / "grub.efi").write_bytes(b"")
            (tdp / "b" / "grub.efi").write_bytes(b"")
            result = c1._probe_first_existing(tdp, [
                "a/grub.efi",
                "b/grub.efi",
            ])
        self.assertEqual(result, tdp / "a" / "grub.efi")


class TestVerifyGrub(unittest.TestCase):
    """Tests for c1.verify_grub()."""

    def _make_grub(self, tdp: Path):
        grub_dir = tdp / "boot" / "efi" / "EFI" / "intergenos"
        grub_dir.mkdir(parents=True)
        grub_efi = grub_dir / "grubx64.efi"
        grub_efi.write_bytes(b"fake PE/COFF")
        return grub_efi

    def test_grub_missing(self):
        with tempfile.TemporaryDirectory() as td:
            result = c1.verify_grub(Path(td), Path("/fake.crt"))
        self.assertEqual(result.stage, "grub")
        self.assertTrue(result.required)
        self.assertFalse(result.sig_present)
        self.assertFalse(result.verified)
        self.assertIn("not found", result.detail)

    def test_grub_present_and_verifies(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            self._make_grub(tdp)
            with patch("subprocess.run", return_value=_fake_completed(0)):
                result = c1.verify_grub(tdp, Path("/fake.crt"))
        self.assertTrue(result.required)
        self.assertTrue(result.sig_present)
        self.assertTrue(result.verified)
        self.assertTrue(result.is_pass())

    def test_grub_present_but_unsigned(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            self._make_grub(tdp)
            with patch("subprocess.run", return_value=_fake_completed(
                1, stderr="no signature table"
            )):
                result = c1.verify_grub(tdp, Path("/fake.crt"))
        self.assertFalse(result.sig_present)
        self.assertFalse(result.verified)
        self.assertFalse(result.is_pass())

    def test_grub_case_insensitive_path(self):
        """FAT32 case-insensitivity: CamelCase EFI/InterGenOS/ is also accepted."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            camel = tdp / "boot" / "efi" / "EFI" / "InterGenOS"
            camel.mkdir(parents=True)
            (camel / "grubx64.efi").write_bytes(b"fake")
            with patch("subprocess.run", return_value=_fake_completed(0)):
                result = c1.verify_grub(tdp, Path("/fake.crt"))
        self.assertTrue(result.verified)
        self.assertIn("InterGenOS", result.path)


class TestVerifyKernels(unittest.TestCase):
    """Tests for c1.verify_kernels()."""

    def test_no_boot_dir(self):
        with tempfile.TemporaryDirectory() as td:
            # No /boot under target
            results = c1.verify_kernels(Path(td), Path("/fake.crt"))
        self.assertEqual(len(results), 1)
        self.assertIn("does not exist", results[0].detail)
        self.assertFalse(results[0].is_pass())

    def test_boot_but_no_vmlinuz(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "boot").mkdir()
            results = c1.verify_kernels(tdp, Path("/fake.crt"))
        self.assertEqual(len(results), 1)
        self.assertIn("no vmlinuz", results[0].detail)
        self.assertFalse(results[0].is_pass())

    def test_multiple_kernels_all_verify(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "boot").mkdir()
            (tdp / "boot" / "vmlinuz-6.18.10").write_bytes(b"fake")
            (tdp / "boot" / "vmlinuz-6.19.0").write_bytes(b"fake")
            with patch("subprocess.run", return_value=_fake_completed(0)):
                results = c1.verify_kernels(tdp, Path("/fake.crt"))
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertTrue(r.is_pass())
            self.assertTrue(r.verified)

    def test_kernels_mixed_pass_fail(self):
        """One signed, one unsigned — mock returns different RCs by binary name."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "boot").mkdir()
            (tdp / "boot" / "vmlinuz-6.18.10").write_bytes(b"fake")
            (tdp / "boot" / "vmlinuz-6.19.0").write_bytes(b"fake")

            def fake_run(cmd, **kwargs):
                # cmd = ["sbverify", "--cert", cert, binary]
                binary = cmd[-1]
                if "6.18.10" in binary:
                    return _fake_completed(0)
                return _fake_completed(1, stderr="no signature table")

            with patch("subprocess.run", side_effect=fake_run):
                results = c1.verify_kernels(tdp, Path("/fake.crt"))

        by_path = {Path(r.path).name: r for r in results}
        self.assertTrue(by_path["vmlinuz-6.18.10"].is_pass())
        self.assertFalse(by_path["vmlinuz-6.19.0"].is_pass())


class TestSkipShim(unittest.TestCase):
    """Tests for c1.skip_shim() — always required=False, descriptive detail."""

    def test_shim_present(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            shim_dir = tdp / "boot" / "efi" / "EFI" / "intergenos"
            shim_dir.mkdir(parents=True)
            (shim_dir / "shimx64.efi").write_bytes(b"fake")
            result = c1.skip_shim(tdp)
        self.assertFalse(result.required)
        self.assertTrue(result.sig_present)
        self.assertFalse(result.verified)
        self.assertTrue(result.is_pass())  # never fails the run
        self.assertIn("Fedora/MS", result.detail)

    def test_shim_absent(self):
        with tempfile.TemporaryDirectory() as td:
            result = c1.skip_shim(Path(td))
        self.assertFalse(result.required)
        self.assertFalse(result.sig_present)
        self.assertTrue(result.is_pass())  # still passes — non-required


class TestRun(unittest.TestCase):
    """Tests for c1.run() — full chain aggregation."""

    def test_clean_target(self):
        """Fully signed target → report passes overall."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            # Make GRUB
            grub_dir = tdp / "boot" / "efi" / "EFI" / "intergenos"
            grub_dir.mkdir(parents=True)
            (grub_dir / "grubx64.efi").write_bytes(b"fake")
            # Make shim (optional)
            (grub_dir / "shimx64.efi").write_bytes(b"fake")
            # Make kernel
            (tdp / "boot" / "vmlinuz-6.18.10").write_bytes(b"fake")

            with patch("subprocess.run", return_value=_fake_completed(0)):
                report = c1.run(tdp, Path("/fake.crt"))

        self.assertTrue(report.all_required_pass())
        stages = [r.stage for r in report.results]
        self.assertEqual(stages, ["shim", "grub", "kernel"])

    def test_kernel_gap(self):
        """Pre-fix target (no kernel sig) → report fails overall."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            grub_dir = tdp / "boot" / "efi" / "EFI" / "intergenos"
            grub_dir.mkdir(parents=True)
            (grub_dir / "grubx64.efi").write_bytes(b"fake")
            (tdp / "boot" / "vmlinuz-6.18.10").write_bytes(b"fake")

            def fake_run(cmd, **kwargs):
                binary = cmd[-1]
                if "grubx64" in binary:
                    return _fake_completed(0)
                # Kernel: no sig (pre-c70642a scenario)
                return _fake_completed(1, stderr="no signature table")

            with patch("subprocess.run", side_effect=fake_run):
                report = c1.run(tdp, Path("/fake.crt"))

        self.assertFalse(report.all_required_pass())
        grub_r = next(r for r in report.results if r.stage == "grub")
        kernel_r = next(r for r in report.results if r.stage == "kernel")
        self.assertTrue(grub_r.is_pass())
        self.assertFalse(kernel_r.is_pass())

    def test_to_dict_shape(self):
        """JSON serialization surface is stable."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "boot").mkdir()
            with patch("subprocess.run", return_value=_fake_completed(0)):
                report = c1.run(tdp, Path("/fake.crt"))
            d = report.to_dict()

        self.assertIn("target", d)
        self.assertIn("mok_cert", d)
        self.assertIn("all_required_pass", d)
        self.assertIn("results", d)
        self.assertIsInstance(d["results"], list)
        # Every result dict has the expected keys
        for r in d["results"]:
            for key in ("stage", "path", "required", "sig_present",
                        "verified", "cert", "detail"):
                self.assertIn(key, r)


class TestMainCLI(unittest.TestCase):
    """Tests for c1.main() — CLI wrapping, exit codes, JSON output."""

    def _make_target(self, tdp: Path, signed: bool = True):
        """Stage a minimal complete target tree. If signed, MOK cert is present."""
        grub_dir = tdp / "boot" / "efi" / "EFI" / "intergenos"
        grub_dir.mkdir(parents=True)
        (grub_dir / "grubx64.efi").write_bytes(b"fake")
        (tdp / "boot" / "vmlinuz-6.18.10").write_bytes(b"fake")
        mok_cert = tdp / "var" / "lib" / "intergen" / "mok" / "mok.crt"
        mok_cert.parent.mkdir(parents=True)
        mok_cert.write_bytes(b"fake cert")
        return mok_cert

    def test_missing_target(self):
        rc = c1.main(["--target", "/definitely/does/not/exist/99"])
        self.assertEqual(rc, 2)

    def test_missing_mok_cert(self):
        with tempfile.TemporaryDirectory() as td:
            # Target exists but has no MOK cert and none passed
            rc = c1.main(["--target", td])
        self.assertEqual(rc, 2)

    def test_all_pass_exit_zero(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            self._make_target(tdp)
            with patch("subprocess.run", return_value=_fake_completed(0)):
                # Suppress stdout for quieter test output
                with patch("sys.stdout", new_callable=io.StringIO):
                    rc = c1.main(["--target", td])
        self.assertEqual(rc, 0)

    def test_any_fail_exit_one(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            self._make_target(tdp)
            with patch("subprocess.run", return_value=_fake_completed(
                1, stderr="no signature table"
            )):
                with patch("sys.stdout", new_callable=io.StringIO):
                    rc = c1.main(["--target", td])
        self.assertEqual(rc, 1)

    def test_report_only_always_zero(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            self._make_target(tdp)
            with patch("subprocess.run", return_value=_fake_completed(
                1, stderr="no signature table"
            )):
                with patch("sys.stdout", new_callable=io.StringIO):
                    rc = c1.main(["--target", td, "--report-only"])
        # Fails verification but --report-only forces 0
        self.assertEqual(rc, 0)

    def test_json_output_parses(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            self._make_target(tdp)
            buf = io.StringIO()
            with patch("subprocess.run", return_value=_fake_completed(0)):
                with patch("sys.stdout", buf):
                    c1.main(["--target", td, "--json"])
            # Should parse cleanly as JSON
            parsed = json.loads(buf.getvalue())
        self.assertTrue(parsed["all_required_pass"])
        self.assertIn("results", parsed)


if __name__ == "__main__":
    unittest.main()
