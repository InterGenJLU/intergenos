"""Integration tests for class1_chain_verify — real openssl + sbsign on disk.

Complements test_class1_chain_verify.py (which mocks subprocess entirely).
These tests require the host to have:
  - openssl
  - sbsign (sbsigntool package)
  - sbverify (sbsigntool package)
  - /usr/share/refind/refind/drivers_x64/ext4_x64.efi (or any small PE32+ EFI
    binary reachable via the TEST_EFI_BINARY env var)

The refind ext4 driver is used as a stand-in for grub/kernel PE binaries:
it's a real 58KB PE32+ EFI binary that sbsign can attach signatures to, and
sbverify can verify against — exercising the full signing path without
requiring a live Forge install or a VM boot.

When the host lacks the tools, every test in this module is skipped (not
failed) so the same suite runs cleanly on laptop + ubuntu2404. Run on
ubuntu2404 (has sbsign + refind) for actual coverage:

    python3 -m unittest installer.tests.test_class1_integration -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from installer.tests import class1_chain_verify as c1


# Default stand-in PE binary — small, world-readable on ubuntu2404 out of box
DEFAULT_TEST_EFI = Path("/usr/share/refind/refind/drivers_x64/ext4_x64.efi")
TEST_EFI = Path(os.environ.get("TEST_EFI_BINARY", str(DEFAULT_TEST_EFI)))


def _tools_present() -> tuple[bool, str]:
    """Return (all-tools-present, reason-if-not)."""
    for tool in ("openssl", "sbsign", "sbverify"):
        if not shutil.which(tool):
            return False, f"{tool} not in PATH"
    if not TEST_EFI.exists():
        return False, f"test EFI binary not found at {TEST_EFI}"
    return True, ""


def _openssl_keypair(out_dir: Path, common_name: str = "test-mok") -> dict:
    """Generate an RSA-2048 self-signed X.509 keypair.

    Mirrors mok.py generate_mok_keypair semantics but runs on the HOST (no
    chroot, no run_chroot). Returns the same {key_path, cert_path, der_path}
    shape for interchange with Forge code if needed.
    """
    key = out_dir / f"{common_name}.key"
    crt = out_dir / f"{common_name}.crt"
    der = out_dir / f"{common_name}.der"
    subprocess.run(
        ["openssl", "req", "-new", "-x509", "-newkey", "rsa:2048",
         "-keyout", str(key), "-out", str(crt), "-outform", "PEM",
         "-days", "30", "-nodes", "-subj", f"/CN={common_name}/"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["openssl", "x509", "-in", str(crt), "-outform", "DER", "-out", str(der)],
        check=True, capture_output=True,
    )
    return {"key_path": str(key), "cert_path": str(crt), "der_path": str(der)}


def _sbsign(binary_in: Path, key: Path, cert: Path, binary_out: Path) -> None:
    subprocess.run(
        ["sbsign", "--key", str(key), "--cert", str(cert),
         "--output", str(binary_out), str(binary_in)],
        check=True, capture_output=True,
    )


def _stage_target(tmpdir: Path) -> dict:
    """Build a minimal target root with GRUB + kernel slots (unsigned).

    Returns dict with paths the tests will fill in:
        target, grub_path, kernel_path
    """
    target = tmpdir / "target"
    grub_dir = target / "boot" / "efi" / "EFI" / "intergenos"
    grub_dir.mkdir(parents=True)
    boot_dir = target / "boot"
    boot_dir.mkdir(parents=True, exist_ok=True)
    return {
        "target": target,
        "grub_path": grub_dir / "grubx64.efi",
        "kernel_path": boot_dir / "vmlinuz-test-6.18.10",
    }


_present, _skip_reason = _tools_present()


@unittest.skipUnless(_present, f"integration prerequisites missing: {_skip_reason}")
class TestClass1Integration(unittest.TestCase):
    """End-to-end Class 1 chain verification against real sbsigned binaries."""

    def test_signed_chain_passes(self):
        """Sign GRUB + kernel with MOK; run class1; expect overall PASS."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            stage = _stage_target(tdp)
            keys = _openssl_keypair(tdp, "mok")

            # Sign our test PE as both grub and kernel slots
            _sbsign(TEST_EFI, Path(keys["key_path"]),
                    Path(keys["cert_path"]), stage["grub_path"])
            _sbsign(TEST_EFI, Path(keys["key_path"]),
                    Path(keys["cert_path"]), stage["kernel_path"])

            report = c1.run(stage["target"], Path(keys["cert_path"]))

        self.assertTrue(report.all_required_pass(),
                        f"expected all-pass; got: {report.to_dict()}")
        grub_r = next(r for r in report.results if r.stage == "grub")
        self.assertTrue(grub_r.sig_present)
        self.assertTrue(grub_r.verified)
        kernel_rs = [r for r in report.results if r.stage == "kernel"]
        self.assertEqual(len(kernel_rs), 1)
        self.assertTrue(kernel_rs[0].verified)

    def test_unsigned_chain_fails(self):
        """Leave GRUB + kernel as raw PE (no sig); expect FAIL."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            stage = _stage_target(tdp)
            keys = _openssl_keypair(tdp, "mok")

            # Raw copy — no sbsign
            shutil.copy(TEST_EFI, stage["grub_path"])
            shutil.copy(TEST_EFI, stage["kernel_path"])

            report = c1.run(stage["target"], Path(keys["cert_path"]))

        self.assertFalse(report.all_required_pass())
        grub_r = next(r for r in report.results if r.stage == "grub")
        self.assertFalse(grub_r.verified)
        # "no signature" should be in the detail (sig block absent)
        self.assertIn("no signature", grub_r.detail.lower())

    def test_wrong_cert_fails(self):
        """Sign with key_A; verify against cert_B; expect FAIL with sig present."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            stage = _stage_target(tdp)
            keys_a = _openssl_keypair(tdp, "mok-a")
            keys_b = _openssl_keypair(tdp, "mok-b")

            # Sign with key A
            _sbsign(TEST_EFI, Path(keys_a["key_path"]),
                    Path(keys_a["cert_path"]), stage["grub_path"])
            _sbsign(TEST_EFI, Path(keys_a["key_path"]),
                    Path(keys_a["cert_path"]), stage["kernel_path"])

            # Verify against cert B (wrong)
            report = c1.run(stage["target"], Path(keys_b["cert_path"]))

        self.assertFalse(report.all_required_pass())
        grub_r = next(r for r in report.results if r.stage == "grub")
        # Signature IS present (sbsign attached one), but doesn't verify
        # against the wrong cert
        self.assertTrue(grub_r.sig_present,
                        "sbsign should have attached a signature block")
        self.assertFalse(grub_r.verified,
                         "verification against wrong cert should fail")

    def test_json_output_shape_real_run(self):
        """End-to-end including JSON serialization sanity check."""
        import json
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            stage = _stage_target(tdp)
            keys = _openssl_keypair(tdp, "mok")
            _sbsign(TEST_EFI, Path(keys["key_path"]),
                    Path(keys["cert_path"]), stage["grub_path"])
            _sbsign(TEST_EFI, Path(keys["key_path"]),
                    Path(keys["cert_path"]), stage["kernel_path"])
            report = c1.run(stage["target"], Path(keys["cert_path"]))
            d = report.to_dict()

        # Round-trip as JSON without loss
        reloaded = json.loads(json.dumps(d))
        self.assertTrue(reloaded["all_required_pass"])
        self.assertEqual(len(reloaded["results"]), 3)  # shim + grub + kernel


if __name__ == "__main__":
    unittest.main()
