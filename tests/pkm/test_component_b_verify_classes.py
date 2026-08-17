# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Component B — verify-honesty classes (named, auditable, evidence-traced).

`pkm verify` on a build chroot flagged ~30 by-design removal absences as
"issues". Component B adds a CLASS-level registry (EXPECTED_ABSENT_CLASSES)
beside the per-package PI-E4 map: each system-wide removal/volatility class is
NAMED, carries the exact producing-script provenance, and is SURFACED per class
in the verify summary — never a blanket mask, never a silent count. A genuinely
missing file outside every pattern still flags; a missing file inside a pattern
but for a package the class does not apply to still flags.

Acceptance criteria implemented here:
  B1 class absences are named (not "missing"); a package whose only absences are
     classed verifies OK; a real loss alongside them still flags.
  B2 the verify summary NAMES the classes (per-class breakdown), not a bare total.
  B3 non-masking: a loss outside every pattern flags; a scoped class does not
     excuse a package it does not apply to.
  B4 citation gate: every registry entry has patterns + provenance + applies_to,
     and its cited producing script exists in-tree and contains the mechanism.
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pkm.database import (
    PackageDB, _is_expected_absent, EXPECTED_ABSENT_CLASSES,
)
from pkm.cli import _expected_absent_note, _merge_expected_absent_classes

REPO_ROOT = Path(__file__).resolve().parents[2]


def _sha(b):
    return hashlib.sha256(b).hexdigest()


class TestClassMatcher(unittest.TestCase):
    def test_ch8_la_sweep_matches_any_package_at_any_depth(self):
        self.assertEqual(
            _is_expected_absent("glib2", "usr/lib/libfoo.la"), "ch8-la-sweep")
        self.assertEqual(
            _is_expected_absent("gcc", "usr/lib/gcc/x86_64/13/libgcc.la"),
            "ch8-la-sweep")
        self.assertEqual(
            _is_expected_absent("openssl", "usr/libexec/foo.la"), "ch8-la-sweep")

    def test_volatile_run_matches_any_package(self):
        self.assertEqual(
            _is_expected_absent("sudo", "run/sudo/ts/root"), "volatile-run")
        self.assertEqual(
            _is_expected_absent("gdm", "run/gdm/greeter.pid"), "volatile-run")

    def test_scoped_prefix_not_over_matched(self):
        # usr/lib64/*.la is a DIFFERENT prefix — the class must not claim it.
        self.assertIsNone(_is_expected_absent("x", "usr/lib64/foo.la"))
        # a real .la-adjacent payload is not a .la file
        self.assertIsNone(_is_expected_absent("x", "usr/lib/libfoo.so"))
        # run-adjacent but not under run/
        self.assertIsNone(_is_expected_absent("x", "usr/run-helper/x"))

    def test_real_payload_never_excused(self):
        self.assertIsNone(_is_expected_absent("bash", "usr/bin/bash"))
        self.assertIsNone(_is_expected_absent("glib2", "usr/lib/libglib-2.0.so"))

    def test_per_package_map_still_named(self):
        # PI-E4 per-package removals return a per-package class id.
        self.assertEqual(
            _is_expected_absent("vte", "etc/profile.d/vte.sh"),
            "post-install:vte")
        self.assertEqual(
            _is_expected_absent(
                "rust", "opt/rustc-1.95.0/share/doc/rustc-1.95.0/LICENSE.old"),
            "post-install:rust")


class TestVerifyClassBreakdown(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pkm-b-test-")
        self.root = Path(self._tmp)
        self.db = PackageDB(db_path=str(self.root / "pkm.db"), root=str(self.root))

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, rel, content=b"x"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    def _install(self, name, files):
        for rel in files:
            self._write(rel)
        pid = self.db.add_installed(name=name, version="1.0",
                                    install_method="archive")
        self.db.add_files(pid, files)
        return pid

    # ---- B1 ---------------------------------------------------------------
    def test_b1_class_absences_named_and_real_loss_flags(self):
        present = "usr/bin/widget"
        la = "usr/lib/widget/libwidget.la"     # ch8-la-sweep
        runp = "run/widget/widget.pid"         # volatile-run
        real = "usr/share/widget/data"         # NOT exempt -> missing
        self._install("widget", [present, la, runp, real])
        # The sweep + tmpfs remove these after registration; the real payload
        # goes genuinely missing.
        (self.root / la).unlink()
        (self.root / runp).unlink()
        (self.root / real).unlink()

        res = self.db.verify_package("widget")
        self.assertEqual(res["expected_absent_by_class"],
                         {"ch8-la-sweep": [la], "volatile-run": [runp]})
        self.assertIn(la, res["expected_absent"])
        self.assertIn(runp, res["expected_absent"])
        self.assertNotIn(la, res["missing"])
        self.assertNotIn(runp, res["missing"])
        # the genuine loss is NOT masked
        self.assertIn(real, res["missing"])

    def test_b1_only_class_absences_verify_ok(self):
        present = "usr/bin/tool"
        la = "usr/lib/tool.la"
        runp = "run/tool/tool.sock"
        self._install("tool", [present, la, runp])
        (self.root / la).unlink()
        (self.root / runp).unlink()
        res = self.db.verify_package("tool")
        self.assertEqual(res["missing"], [])       # 0 spurious issues
        self.assertEqual(res["modified"], [])
        self.assertEqual(sorted(res["expected_absent"]), sorted([la, runp]))

    # ---- B2 ---------------------------------------------------------------
    def test_b2_summary_names_classes(self):
        agg = {}
        _merge_expected_absent_classes(
            agg, {"ch8-la-sweep": ["a.la", "b.la"], "volatile-run": ["run/x"]})
        note = _expected_absent_note(agg)
        self.assertEqual(note, "; 3 expected-absent: 2 ch8-la-sweep, 1 volatile-run")
        self.assertEqual(_expected_absent_note({}), "")

    # ---- B3 ---------------------------------------------------------------
    def test_b3_real_loss_outside_patterns_flags(self):
        self._install("svc", ["usr/bin/svc", "usr/lib/svc/plugin.so"])
        (self.root / "usr/lib/svc/plugin.so").unlink()  # .so, not .la
        res = self.db.verify_package("svc")
        self.assertIn("usr/lib/svc/plugin.so", res["missing"])
        self.assertEqual(res["expected_absent"], [])

    def test_b3_scoped_class_does_not_excuse_other_packages(self):
        # A package-SCOPED class must not blind a package it does not apply to.
        scoped = dict(EXPECTED_ABSENT_CLASSES)
        scoped["demo-scoped"] = {
            "patterns": ("usr/share/demo/*",),
            "provenance": "test-only scoped class",
            "applies_to": ("demopkg",),
        }
        with mock.patch("pkm.database.EXPECTED_ABSENT_CLASSES", scoped):
            # applies to demopkg
            self.assertEqual(
                _is_expected_absent("demopkg", "usr/share/demo/x"),
                "demo-scoped")
            # but NOT to another package sharing the path -> still a real loss
            self.assertIsNone(_is_expected_absent("otherpkg", "usr/share/demo/x"))


class TestCitationGate(unittest.TestCase):
    # ---- B4 ---------------------------------------------------------------
    def test_every_class_has_patterns_provenance_applies_to(self):
        for cid, spec in EXPECTED_ABSENT_CLASSES.items():
            self.assertTrue(spec.get("patterns"), f"{cid}: patterns required")
            self.assertTrue(spec.get("provenance"), f"{cid}: provenance required")
            self.assertIn("applies_to", spec, f"{cid}: applies_to required")

    def test_provenance_scripts_exist_and_contain_mechanism(self):
        # The producing script cited in each provenance must exist in-tree, and
        # the mechanism the class claims must be present in it (the citation
        # gate — no exemption without a resolvable, real producing line).
        ch8 = REPO_ROOT / "scripts/chroot-build-ch8.sh"
        self.assertTrue(ch8.is_file(), "ch8 build script must exist")
        self.assertIn("-name \\*.la -delete", ch8.read_text(),
                      "the .la sweep mechanism must be present in ch8")

        squashfs = REPO_ROOT / "scripts/build-squashfs.sh"
        self.assertTrue(squashfs.is_file(), "squashfs script must exist")
        sq = squashfs.read_text()
        self.assertIn("/run", sq)
        self.assertIn("tmpfs", sq)


if __name__ == "__main__":
    unittest.main()
