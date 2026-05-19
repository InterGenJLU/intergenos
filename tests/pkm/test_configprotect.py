#!/usr/bin/env python3
"""Integration-style tests for pkm.configprotect + Q4 baseline-tracking DB.

Covers the SPOC-side wiring for Q4 (O-006 user-modified config-file silent
overwrite + O-021 original_checksum baseline ratchet):

Database layer:
  - original_checksum recorded ONCE at first install (not ratcheted on
    re-register during upgrade — the O-021 fix at database.py:264)
  - get_original_checksum / update_original_checksum / refresh_baseline
    primitives the upgrade orchestration relies on

configprotect orchestration:
  - prepare_config_protection classifies archive /etc/* paths into
    {first-install, unedited (ratchet), user-edited (protect+sidecar)}
  - materialize_pkmnew_sidecars writes the .pkmnew files for protected paths
  - ratchet_baselines updates DB for unedited paths after deploy
  - summary_lines produces the end-of-upgrade batch summary

Tests use a real SQLite DB (PackageDB) in a tempdir + a real filesystem
layout for the staging + live roots, exercising the actual sqlite + sha256
+ shutil.copy paths. Per integration-style sanity check standard (D-009
item 8 + arc lessons), py_compile / import-resolution alone are
insufficient for cross-module data-contract surfaces; these tests assert
GREEN + RED paths through the real code.
"""

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB, _sha256
from pkm.configprotect import (
    prepare_config_protection,
    materialize_pkmnew_sidecars,
    ratchet_baselines,
    summary_lines,
)


def _sha_of(content):
    return hashlib.sha256(content.encode() if isinstance(content, str) else content).hexdigest()


class TestBaselineTrackingDB(unittest.TestCase):
    """Database-layer tests for the O-021 ratchet fix + helper methods."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-q4-db-")
        self.live_root = Path(self.tmp) / "root"
        self.live_root.mkdir()
        (self.live_root / "etc").mkdir()
        self.db = PackageDB(Path(self.tmp) / "test.db", root=str(self.live_root))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_config(self, rel, content):
        full = self.live_root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return _sha_of(content)

    def _install_pkg_with_config(self, name, version, rel, content):
        """Set up: deploy file to live, register package + files in DB."""
        sha = self._create_config(rel, content)
        pkg_id = self.db.add_installed(
            name=name, version=version, install_method="archive",
            archive_path="/dev/null", commit=False,
        )
        self.db.add_files(pkg_id, [rel], hashes={rel: sha}, commit=False)
        self.db.conn.commit()
        return pkg_id, sha

    def test_original_checksum_recorded_on_first_install(self):
        _, expected_sha = self._install_pkg_with_config(
            "foo", "1.0", "etc/foo.conf", "key=initial"
        )
        recorded = self.db.get_original_checksum("etc/foo.conf")
        self.assertEqual(recorded, expected_sha)

    def test_original_checksum_NOT_ratcheted_on_reregister(self):
        # O-021 regression test. First install records sha_initial.
        # A subsequent add_files re-register with a different hash (simulating
        # upgrade-time add_files-after-deploy) MUST NOT overwrite the recorded
        # original_checksum.
        pkg_id, sha_initial = self._install_pkg_with_config(
            "foo", "1.0", "etc/foo.conf", "key=initial"
        )
        # Simulate upgrade: deploy new stock, re-register
        new_sha = self._create_config("etc/foo.conf", "key=new_stock")
        self.db.add_files(pkg_id, ["etc/foo.conf"], hashes={"etc/foo.conf": new_sha}, commit=True)
        recorded = self.db.get_original_checksum("etc/foo.conf")
        self.assertEqual(
            recorded, sha_initial,
            "original_checksum must remain at sha_initial after re-register "
            "(the O-021 baseline ratchet fix)"
        )

    def test_reregister_updates_package_id_only(self):
        # Re-register with a different package_id (e.g. package renamed during
        # an upgrade) — the package_id reference should update to the new owner.
        sha = self._create_config("etc/foo.conf", "key=value")
        pkg1 = self.db.add_installed("foo", "1.0", "archive", "/dev/null", commit=True)
        self.db.add_files(pkg1, ["etc/foo.conf"], hashes={"etc/foo.conf": sha}, commit=True)
        pkg2 = self.db.add_installed("foo-renamed", "1.0", "archive", "/dev/null", commit=True)
        self.db.add_files(pkg2, ["etc/foo.conf"], hashes={"etc/foo.conf": sha}, commit=True)
        row = self.db.conn.execute(
            "SELECT package_id, original_checksum FROM config_files WHERE path = ?",
            ("etc/foo.conf",)
        ).fetchone()
        self.assertEqual(row[0], pkg2, "package_id should update to new owner")
        self.assertEqual(row[1], sha, "original_checksum should not change")

    def test_get_original_checksum_returns_none_for_untracked(self):
        self.assertIsNone(self.db.get_original_checksum("etc/nonexistent.conf"))

    def test_get_original_checksum_accepts_leading_slash(self):
        _, expected = self._install_pkg_with_config("foo", "1.0", "etc/foo.conf", "x")
        self.assertEqual(self.db.get_original_checksum("/etc/foo.conf"), expected)

    def test_update_original_checksum_explicit_ratchet(self):
        _, _ = self._install_pkg_with_config("foo", "1.0", "etc/foo.conf", "initial")
        new_sha = _sha_of("new-stock-content")
        rowcount = self.db.update_original_checksum("etc/foo.conf", new_sha)
        self.assertEqual(rowcount, 1)
        self.assertEqual(self.db.get_original_checksum("etc/foo.conf"), new_sha)

    def test_update_original_checksum_returns_zero_for_untracked(self):
        rowcount = self.db.update_original_checksum("etc/nonexistent", _sha_of("x"))
        self.assertEqual(rowcount, 0)

    def test_refresh_baseline_recomputes_from_live(self):
        # First install records sha_initial. User then writes new content to
        # the live file (simulating mv foo.conf.pkmnew foo.conf). After
        # refresh_baseline, the recorded sha matches new content.
        _, sha_initial = self._install_pkg_with_config("foo", "1.0", "etc/foo.conf", "initial")
        (self.live_root / "etc/foo.conf").write_text("user-accepted-new")
        success, msg = self.db.refresh_baseline("etc/foo.conf")
        self.assertTrue(success)
        recorded = self.db.get_original_checksum("etc/foo.conf")
        self.assertEqual(recorded, _sha_of("user-accepted-new"))
        self.assertNotEqual(recorded, sha_initial)

    def test_refresh_baseline_missing_file_fails(self):
        # No config_files row at all → fails-not-tracked.
        success, msg = self.db.refresh_baseline("etc/never-existed.conf")
        self.assertFalse(success)
        self.assertIn("file not found", msg)

    def test_refresh_baseline_untracked_file_fails(self):
        # Live file exists but no config_files row → fails-not-tracked.
        (self.live_root / "etc/orphan.conf").write_text("hi")
        success, msg = self.db.refresh_baseline("etc/orphan.conf")
        self.assertFalse(success)
        self.assertIn("not tracked", msg)


class TestPrepareConfigProtection(unittest.TestCase):
    """configprotect.prepare_config_protection planning logic."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-q4-prep-")
        self.live_root = Path(self.tmp) / "root"
        self.live_root.mkdir()
        (self.live_root / "etc").mkdir()
        self.staging = Path(self.tmp) / "staging"
        self.staging.mkdir()
        (self.staging / "etc").mkdir()
        self.db = PackageDB(Path(self.tmp) / "test.db", root=str(self.live_root))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stage(self, rel, content):
        path = self.staging / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def _live(self, rel, content):
        path = self.live_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def _register_baseline(self, rel, content):
        # Set up a tracked config file with original_checksum = sha(content).
        pkg_id = self.db.add_installed("foo", "1.0", "archive", "/dev/null", commit=True)
        sha = _sha_of(content)
        self.db.conn.execute(
            "INSERT INTO config_files (path, package_id, original_checksum) VALUES (?, ?, ?)",
            (rel, pkg_id, sha),
        )
        self.db.conn.commit()
        return sha

    def test_first_install_no_action(self):
        # New archive ships etc/new.conf; nothing exists on live, nothing tracked.
        self._stage("etc/new.conf", "stock")
        plan = prepare_config_protection(
            self.staging, ["etc/new.conf"], self.live_root, self.db
        )
        self.assertEqual(plan["protect"], [])
        self.assertEqual(plan["update_baselines"], {})
        self.assertEqual(plan["pkmnew_writes"], [])

    def test_stale_baseline_live_missing_ratchets(self):
        # Sub-case (b) of live-missing: a recorded baseline exists from a
        # prior install (config_files row persisted across remove via
        # ON DELETE SET NULL on package_id), but live was removed by the
        # previous remove operation. Without the ratchet plan, the post-
        # deploy live (new stock) would diverge from the recorded baseline
        # (old stock) and the NEXT upgrade would wrongly classify as
        # user-edited. Caught in peer-review of windows-host coordinator's
        # d65081b4 installer wiring.
        sha_v1 = self._register_baseline("etc/foo.conf", "stock-v1")
        # Live file removed by prior remove operation (simulated by not
        # creating the live file). Staging ships stock-v2.
        self._stage("etc/foo.conf", "stock-v2")
        plan = prepare_config_protection(
            self.staging, ["etc/foo.conf"], self.live_root, self.db
        )
        self.assertEqual(plan["protect"], [])
        self.assertEqual(plan["pkmnew_writes"], [])
        # Ratchet planned — baseline will advance to stock-v2 after deploy.
        self.assertEqual(plan["update_baselines"], {"etc/foo.conf": _sha_of("stock-v2")})

    def test_unedited_plans_baseline_ratchet(self):
        # Live + baseline both equal "stock-v1"; staging ships "stock-v2".
        # User hasn't edited → unedited path, baseline ratchets to v2 sha.
        sha_v1 = self._register_baseline("etc/foo.conf", "stock-v1")
        self._live("etc/foo.conf", "stock-v1")
        self._stage("etc/foo.conf", "stock-v2")
        plan = prepare_config_protection(
            self.staging, ["etc/foo.conf"], self.live_root, self.db
        )
        self.assertEqual(plan["protect"], [])
        self.assertEqual(plan["pkmnew_writes"], [])
        self.assertEqual(plan["update_baselines"], {"etc/foo.conf": _sha_of("stock-v2")})

    def test_user_edited_plans_protect_and_sidecar(self):
        # Baseline = "stock-v1", but live has "user-edited" (different sha).
        # Archive ships "stock-v2". User has edited → protect + sidecar.
        self._register_baseline("etc/foo.conf", "stock-v1")
        self._live("etc/foo.conf", "user-edited")
        self._stage("etc/foo.conf", "stock-v2")
        plan = prepare_config_protection(
            self.staging, ["etc/foo.conf"], self.live_root, self.db
        )
        self.assertEqual(plan["protect"], ["etc/foo.conf"])
        self.assertEqual(plan["update_baselines"], {})
        self.assertEqual(len(plan["pkmnew_writes"]), 1)
        src, dest = plan["pkmnew_writes"][0]
        self.assertTrue(src.endswith("staging/etc/foo.conf"))
        self.assertTrue(dest.endswith("root/etc/foo.conf.pkmnew"))

    def test_no_baseline_treated_as_unedited(self):
        # No config_files row yet, but live file exists. Treat as unedited
        # (best-effort fallback for legacy installs without baseline records).
        self._live("etc/legacy.conf", "live-content")
        self._stage("etc/legacy.conf", "new-stock")
        plan = prepare_config_protection(
            self.staging, ["etc/legacy.conf"], self.live_root, self.db
        )
        self.assertEqual(plan["protect"], [])
        self.assertEqual(
            plan["update_baselines"], {"etc/legacy.conf": _sha_of("new-stock")}
        )

    def test_non_etc_paths_ignored(self):
        # Archive ships /usr/bin/foo + /usr/share/x — not /etc/* — no action.
        self._stage("usr/bin/foo", "binary")
        self._stage("usr/share/foo/data", "data")
        plan = prepare_config_protection(
            self.staging, ["usr/bin/foo", "usr/share/foo/data"], self.live_root, self.db
        )
        self.assertEqual(plan["protect"], [])
        self.assertEqual(plan["update_baselines"], {})

    def test_directories_skipped(self):
        plan = prepare_config_protection(
            self.staging, ["etc/foo.d/"], self.live_root, self.db
        )
        self.assertEqual(plan["protect"], [])

    def test_multiple_paths_classified_independently(self):
        sha_a = self._register_baseline("etc/a.conf", "a-v1")
        self._live("etc/a.conf", "a-v1")             # unedited
        self._stage("etc/a.conf", "a-v2")
        self._register_baseline("etc/b.conf", "b-v1")
        self._live("etc/b.conf", "b-edited")          # user-edited
        self._stage("etc/b.conf", "b-v2")
        self._stage("etc/c.conf", "c-v1")             # first install
        plan = prepare_config_protection(
            self.staging, ["etc/a.conf", "etc/b.conf", "etc/c.conf"],
            self.live_root, self.db,
        )
        self.assertEqual(plan["protect"], ["etc/b.conf"])
        self.assertEqual(plan["update_baselines"], {"etc/a.conf": _sha_of("a-v2")})
        self.assertEqual(len(plan["pkmnew_writes"]), 1)


class TestMaterializeSidecars(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-q4-mat-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_pkmnew_from_staging(self):
        src = Path(self.tmp) / "staging" / "etc" / "foo.conf"
        src.parent.mkdir(parents=True)
        src.write_text("new-stock-content")
        dest = Path(self.tmp) / "root" / "etc" / "foo.conf.pkmnew"
        # parent of dest doesn't exist yet — materialize must mkdir
        written = materialize_pkmnew_sidecars([(str(src), str(dest))])
        self.assertEqual(written, [str(dest)])
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.read_text(), "new-stock-content")

    def test_empty_input_returns_empty(self):
        self.assertEqual(materialize_pkmnew_sidecars([]), [])

    def test_failure_continues_with_remaining(self):
        # First entry has unwritable dest (path under /); second succeeds.
        src_a = Path(self.tmp) / "a"
        src_a.write_text("a")
        src_b = Path(self.tmp) / "b"
        src_b.write_text("b")
        dest_a = "/proc/cannot-write-here/sidecar.pkmnew"
        dest_b = Path(self.tmp) / "ok.pkmnew"
        written = materialize_pkmnew_sidecars([(str(src_a), dest_a), (str(src_b), str(dest_b))])
        self.assertEqual(written, [str(dest_b)])
        self.assertTrue(dest_b.is_file())


class TestRatchetBaselines(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-q4-ratchet-")
        self.live_root = Path(self.tmp) / "root"
        self.live_root.mkdir()
        self.db = PackageDB(Path(self.tmp) / "test.db", root=str(self.live_root))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ratchet_updates_recorded_baseline(self):
        pkg_id = self.db.add_installed("foo", "1.0", "archive", "/dev/null", commit=True)
        sha_old = _sha_of("v1")
        self.db.conn.execute(
            "INSERT INTO config_files (path, package_id, original_checksum) VALUES (?, ?, ?)",
            ("etc/foo.conf", pkg_id, sha_old),
        )
        self.db.conn.commit()
        new_sha = _sha_of("v2")
        ratchet_baselines(self.db, {"etc/foo.conf": new_sha})
        self.assertEqual(self.db.get_original_checksum("etc/foo.conf"), new_sha)

    def test_ratchet_empty_dict_no_op(self):
        # Should not raise.
        ratchet_baselines(self.db, {})


class TestSummaryLines(unittest.TestCase):

    def test_empty_returns_empty_string(self):
        self.assertEqual(summary_lines([]), "")

    def test_single_path_includes_count_and_path(self):
        s = summary_lines(["/etc/foo.conf.pkmnew"])
        self.assertIn("(1)", s)
        self.assertIn("/etc/foo.conf.pkmnew", s)
        self.assertIn("refresh-baseline", s)

    def test_multiple_paths_sorted(self):
        s = summary_lines([
            "/etc/zebra.conf.pkmnew",
            "/etc/alpha.conf.pkmnew",
            "/etc/middle.conf.pkmnew",
        ])
        self.assertIn("(3)", s)
        # Sorted order — alpha before middle before zebra
        i_alpha = s.index("alpha")
        i_middle = s.index("middle")
        i_zebra = s.index("zebra")
        self.assertLess(i_alpha, i_middle)
        self.assertLess(i_middle, i_zebra)


if __name__ == "__main__":
    unittest.main()
