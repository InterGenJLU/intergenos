#!/usr/bin/env python3
"""Regression test for PI-E4 / Class 4 — the expected-absent verify status.

A package's own post_install hook removes/relocates files the build's
filesystem-diff snapshot recorded as owned (rust `rm *.old`, ghostscript
relocates its versioned doc dir, pulseaudio drops X11/autostart launchers, vte
`rm /etc/profile.d/vte.*`). Those files are legitimately absent on a clean
install. verify must report them as a distinct "expected-absent" status, NOT
"missing", while ANY OTHER absent owned file still surfaces as "missing" — no
blind drop, so a genuinely-missing file is never masked.
"""

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB, _is_expected_absent
from pkm.verifier import PackageVerifier, EXIT_OK


def _sha(b):
    return hashlib.sha256(b).hexdigest()


class TestExpectedAbsentMatcher(unittest.TestCase):
    def test_known_patterns_match(self):
        self.assertTrue(_is_expected_absent(
            "rust", "opt/rustc-1.95.0/share/doc/rustc-1.95.0/LICENSE-MIT.old"))
        self.assertTrue(_is_expected_absent(
            "ghostscript", "usr/share/doc/ghostscript/10.06.0/COPYING"))
        # version-robust: a future version still matches the glob
        self.assertTrue(_is_expected_absent(
            "ghostscript", "usr/share/doc/ghostscript/99.9/Ghostscript.pdf"))
        self.assertTrue(_is_expected_absent(
            "pulseaudio", "etc/xdg/autostart/pulseaudio.desktop"))
        self.assertTrue(_is_expected_absent("vte", "etc/profile.d/vte.sh"))
        self.assertTrue(_is_expected_absent("vte", "etc/profile.d/vte.csh"))

    def test_scoped_to_package(self):
        # The same path under a DIFFERENT package is NOT excused (scoping
        # prevents one package's exemption from blinding another's verify).
        self.assertFalse(_is_expected_absent(
            "evil", "usr/share/doc/ghostscript/10.06.0/COPYING"))
        # An unlisted package has no exemptions at all.
        self.assertFalse(_is_expected_absent("bash", "usr/bin/bash"))

    def test_live_payload_not_excused(self):
        # Only the hook-removed files are excused, never the package's real
        # payload — so a genuinely-missing binary/lib still surfaces.
        self.assertFalse(_is_expected_absent("rust", "opt/rustc-1.95.0/bin/rustc"))
        self.assertFalse(_is_expected_absent(  # README.md (no .old) is not excused
            "rust", "opt/rustc-1.95.0/share/doc/rustc-1.95.0/README.md"))
        self.assertFalse(_is_expected_absent("vte", "usr/lib/libvte.so"))


class TestVerifyExpectedAbsentVsMissing(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pkm-ea-test-")
        self.root = Path(self._tmp)
        self.db = PackageDB(db_path=str(self.root / "pkm.db"), root=str(self.root))

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, rel, content):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    def test_expected_absent_separated_from_missing(self):
        present = "usr/lib/libvte.so"
        expected_absent = "etc/profile.d/vte.sh"          # vte's hook removes it
        genuinely_missing = "usr/share/vte/should-exist"  # NOT exempt
        self._write(present, b"lib")
        pkg_id = self.db.add_installed(
            name="vte", version="0.78", install_method="archive")
        self.db.add_files(
            pkg_id, [present, expected_absent, genuinely_missing],
            hashes={present: _sha(b"lib"),
                    expected_absent: _sha(b"x"),
                    genuinely_missing: _sha(b"y")})

        result = self.db.verify_package("vte")
        # the hook-removed file is expected-absent, NOT missing
        self.assertIn(expected_absent, result["expected_absent"])
        self.assertNotIn(expected_absent, result["missing"])
        # the non-exempt absent file STILL flags missing (no masking)
        self.assertIn(genuinely_missing, result["missing"])
        # the present file is in neither bucket
        self.assertNotIn(present, result["missing"])
        self.assertNotIn(present, result["expected_absent"])

    def test_only_expected_absent_verifies_ok(self):
        # A package whose only absences are expected-absent must verify OK
        # (the status must not fail the package).
        present = "usr/lib/libvte.so"
        ea = "etc/profile.d/vte.csh"
        self._write(present, b"lib")
        pkg_id = self.db.add_installed(
            name="vte", version="0.78", install_method="archive")
        self.db.add_files(pkg_id, [present, ea],
                          hashes={present: _sha(b"lib"), ea: _sha(b"z")})

        res = PackageVerifier(self.db).verify("vte")
        self.assertEqual(res["expected_absent"], [ea])
        self.assertEqual(res["missing"], [])
        self.assertEqual(res["exit_code"], EXIT_OK)


if __name__ == "__main__":
    unittest.main()
