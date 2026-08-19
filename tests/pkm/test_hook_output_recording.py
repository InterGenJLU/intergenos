#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""What a lifecycle hook writes is owned by the package that shipped it (D-9).

Sealing a recipe's post_install() into the signed archive closes EXECUTION.
It does not close OWNERSHIP: a hook that generates a cache or an index
writes files no manifest declares, and before this recorder those files were
owned by nobody — `pkm provides` denied them, `pkm remove` stranded them,
`pkm verify` never looked at them, and the squashfs ownership gate, which
reads the same files table, either refused the image or shipped them
unaccounted. Closing execution without closing ownership relocates the
unowned-file class rather than eliminating it.

These tests pin the measurement (pkm/hookrecord.py), the attribution rule,
and the real install path end to end — including the round trip that starts
at a recipe function and ends with the hook's output registered.

Three properties here are the ones that would fail silently if broken:

  * ctime, not mtime, is the modification signal. A hook that rewrites a
    file and restores its mtime is still observed.
  * A file the hook created that ANOTHER package already owns is not
    claimed. Claiming it would fabricate co-ownership and let a remove of
    the wrong package unlink someone else's file.
  * The text manifest carries the generated paths. `pkm import` rebuilds
    file rows FROM the manifest after cascading the old ones away, and it
    runs corpus-wide after every bash-tier package build — a row that is
    in the database but not in the manifest survives until then and no
    longer.
"""
from __future__ import annotations

import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from pkm import hookrecord
from pkm.database import PackageDB, _sha256
from pkm.installer import PackageInstaller
from pkm.remover import PackageRemover


def _touch(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


class SnapshotTests(unittest.TestCase):
    """The measurement itself."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        self.root.mkdir()

    def test_prune_list_is_not_observed(self):
        for rel in ("proc/cpuinfo", "sys/kernel/x", "dev/null.txt",
                    "run/lock/x", "tmp/scratch", "var/tmp/scratch",
                    "var/log/messages", "var/lib/igos/pkm.db",
                    "var/lib/chronicle/blob", "root/.cache/x",
                    "home/user/.bashrc"):
            _touch(self.root / rel)
        _touch(self.root / "usr/bin/real")
        snap = hookrecord.fs_snapshot(self.root)
        self.assertIn("usr/bin/real", snap)
        for rel in list(snap):
            top = rel.split("/")[0]
            self.assertNotIn(
                top, {"proc", "sys", "dev", "run", "tmp", "home"},
                f"{rel} came from a pruned tree")
        for rel in ("var/tmp/scratch", "var/log/messages",
                    "var/lib/igos/pkm.db", "var/lib/chronicle/blob",
                    "root/.cache/x"):
            self.assertNotIn(rel, snap, rel)

    def test_prune_list_is_non_empty(self):
        """A prune set that pruned nothing would pass the test above by
        accident on a tree that happened to have no such dirs."""
        self.assertIn("var/lib/igos", hookrecord.SNAPSHOT_PRUNE_DEFAULT)
        self.assertGreater(len(hookrecord.SNAPSHOT_PRUNE_DEFAULT), 5)

    def test_created_file_dir_and_symlink(self):
        _touch(self.root / "usr/bin/before")
        before = hookrecord.fs_snapshot(self.root)
        _touch(self.root / "var/cache/demo/index")
        os.symlink("index", str(self.root / "var/cache/demo/latest"))
        created_files, created_dirs, modified = hookrecord.diff_snapshots(
            before, hookrecord.fs_snapshot(self.root))
        self.assertIn("var/cache/demo/index", created_files)
        self.assertIn("var/cache/demo/latest", created_files)
        self.assertIn("var/cache/demo/", created_dirs)
        self.assertIn("var/cache/", created_dirs)
        self.assertEqual(modified, [])

    def test_modification_is_seen_through_a_restored_mtime(self):
        """ctime is the signal precisely because userland cannot set it.

        A same-size rewrite with the mtime put back is what a careless
        mtime-keyed diff misses entirely.
        """
        target = _touch(self.root / "usr/share/demo/data", "AAAA")
        st = os.lstat(target)
        before = hookrecord.fs_snapshot(self.root)
        target.write_text("BBBB")
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(os.lstat(target).st_mtime_ns, st.st_mtime_ns)

        created, _, modified = hookrecord.diff_snapshots(
            before, hookrecord.fs_snapshot(self.root))
        self.assertEqual(created, [])
        self.assertEqual(modified, ["usr/share/demo/data"])

    def test_unchanged_tree_diffs_to_nothing(self):
        _touch(self.root / "usr/bin/x")
        before = hookrecord.fs_snapshot(self.root)
        self.assertEqual(
            hookrecord.diff_snapshots(before,
                                      hookrecord.fs_snapshot(self.root)),
            ([], [], []))

    def test_claimable_leaves_another_owners_path_alone(self):
        claim, foreign = hookrecord.claimable(
            ["usr/share/mine", "usr/share/theirs"], {"usr/share/theirs"})
        self.assertEqual(claim, ["usr/share/mine"])
        self.assertEqual(foreign, ["usr/share/theirs"])

    def test_claimable_matches_a_directory_against_its_stored_form(self):
        """Directory rows are stored without the trailing slash."""
        claim, foreign = hookrecord.claimable(
            ["var/cache/demo/"], {"var/cache/demo"})
        self.assertEqual(claim, [])
        self.assertEqual(foreign, ["var/cache/demo/"])


class _InstallHarness(unittest.TestCase):
    """A real PackageInstaller over a real archive on a real root."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "t.db", root=str(self.root))
        self.installer = PackageInstaller(self.db, root=str(self.root))

    def tearDown(self):
        self.db.close()

    def _archive(self, name="demo", version="1.0", hook_body=None,
                 payload=("usr/bin/demo", "payload\n")):
        stg = self.tmp / f"stg-{name}-{version}"
        rel, content = payload
        _touch(stg / rel, content)
        (stg / ".PKGINFO").write_text(
            f"pkgname = {name}\npkgver = {version}\n")
        if hook_body is not None:
            scripts = stg / ".scripts"
            scripts.mkdir()
            hook = scripts / "post_install.sh"
            hook.write_text("#!/bin/bash\nset -e\n" + hook_body)
            hook.chmod(0o755)
        arc = self.tmp / f"{name}-{version}.igos.tar.gz"
        with tarfile.open(arc, "w:gz") as tf:
            tf.add(stg, arcname=".")
        return arc

    def _owned(self, path):
        return self.db.conn.execute(
            "SELECT is_generated FROM files WHERE path = ?", (path,),
        ).fetchone()


class RecordingTests(_InstallHarness):

    HOOK = (
        'mkdir -p "$PKM_PACKAGE_ROOT/var/cache/demo"\n'
        'printf generated > "$PKM_PACKAGE_ROOT/var/cache/demo/index"\n'
    )

    def test_hook_output_is_owned_by_the_package_that_shipped_the_hook(self):
        ok, msg = self.installer.install(
            "demo", archive_path=str(self._archive(hook_body=self.HOOK)))
        self.assertTrue(ok, msg)

        self.assertTrue((self.root / "var/cache/demo/index").is_file())
        row = self._owned("var/cache/demo/index")
        self.assertIsNotNone(
            row, "the hook's output is on disk owned by nobody — the exact "
                 "unowned-file class this recorder exists to close")
        self.assertEqual(row[0], 1, "recorded, but not AS generated")

        owner = self.db.find_owner("/var/cache/demo/index")
        self.assertIsNotNone(owner)
        self.assertEqual(owner["name"], "demo")

    def test_the_created_directory_is_owned_too(self):
        """Otherwise `pkm remove` leaves the empty-directory class behind."""
        ok, msg = self.installer.install(
            "demo", archive_path=str(self._archive(hook_body=self.HOOK)))
        self.assertTrue(ok, msg)
        row = self.db.conn.execute(
            "SELECT is_dir, is_generated FROM files WHERE path = ?",
            ("var/cache/demo",)).fetchone()
        self.assertIsNotNone(row, "hook-created directory left unowned")
        self.assertEqual((row[0], row[1]), (1, 1))

    def test_remove_takes_the_hook_output_with_it(self):
        ok, msg = self.installer.install(
            "demo", archive_path=str(self._archive(hook_body=self.HOOK)))
        self.assertTrue(ok, msg)
        generated = self.root / "var/cache/demo/index"
        self.assertTrue(generated.is_file())

        ok, msg = PackageRemover(self.db, root=str(self.root)).remove(
            "demo", force=True)
        self.assertTrue(ok, msg)
        self.assertFalse(
            generated.exists(),
            "remove left the hook's output on disk — an orphan pkm can no "
            "longer see, because its owning row went with the package")

    def test_a_package_without_a_hook_records_nothing(self):
        ok, msg = self.installer.install(
            "demo", archive_path=str(self._archive()))
        self.assertTrue(ok, msg)
        gen = self.db.conn.execute(
            "SELECT COUNT(*) FROM files WHERE is_generated = 1").fetchone()[0]
        self.assertEqual(gen, 0)

    def test_a_package_without_a_hook_never_walks_the_tree(self):
        """The whole-tree diff is affordable only because ~99% skip it."""
        calls = []
        real = hookrecord.fs_snapshot
        try:
            import pkm.installer as inst
            inst.fs_snapshot = lambda *a, **k: (calls.append(a) or real(*a, **k))
            ok, msg = self.installer.install(
                "demo", archive_path=str(self._archive()))
            self.assertTrue(ok, msg)
            self.assertEqual(calls, [],
                             "a hookless install paid for a full-tree walk")
        finally:
            inst.fs_snapshot = real

    def test_a_hook_output_another_package_owns_is_not_stolen(self):
        # alpha owns the path first, and its file is then deleted so the
        # hook is the one that (re-)creates it.
        alpha = self.db.add_installed("alpha", "1.0", release=1, tier="core")
        contested = _touch(self.root / "usr/share/contested", "alpha's\n")
        self.db.add_files(alpha, ["usr/share/contested"],
                          hashes={"usr/share/contested": _sha256(str(contested))})
        contested.unlink()

        ok, msg = self.installer.install("demo", archive_path=str(self._archive(
            hook_body='printf hooked > "$PKM_PACKAGE_ROOT/usr/share/contested"\n')))
        self.assertTrue(ok, msg)

        rows = self.db.conn.execute(
            "SELECT i.name, f.is_generated FROM files f "
            "JOIN installed i ON f.package_id = i.id WHERE f.path = ?",
            ("usr/share/contested",)).fetchall()
        self.assertEqual(rows, [("alpha", 0)],
                         "the hook's write over another package's path was "
                         "claimed as this package's own")

    def test_a_pre_existing_unowned_file_the_hook_only_touches_is_not_claimed(self):
        stray = _touch(self.root / "usr/share/stray", "AAAA")
        ok, msg = self.installer.install("demo", archive_path=str(self._archive(
            hook_body='printf BBBB > "$PKM_PACKAGE_ROOT/usr/share/stray"\n')))
        self.assertTrue(ok, msg)
        self.assertEqual(stray.read_text(), "BBBB")
        self.assertIsNone(
            self._owned("usr/share/stray"),
            "a file that existed before the hook ran was not created by it; "
            "claiming it would absorb a pre-existing unowned file under "
            "whichever package happened to run a hook near it")


class VerifyTests(_InstallHarness):

    HOOK = ('mkdir -p "$PKM_PACKAGE_ROOT/var/cache/demo"\n'
            'printf first > "$PKM_PACKAGE_ROOT/var/cache/demo/index"\n')

    def _install(self):
        ok, msg = self.installer.install(
            "demo", archive_path=str(self._archive(hook_body=self.HOOK)))
        self.assertTrue(ok, msg)

    def test_regenerated_content_is_not_reported_modified(self):
        self._install()
        # What the next run of the same cache refresh does — routinely
        # triggered by another package's install, not by tampering.
        (self.root / "var/cache/demo/index").write_text("second")

        result = self.db.verify_package("demo", strict=True)
        self.assertEqual(result["modified"], [])
        self.assertIn("var/cache/demo/index", result["generated"])

    def test_generated_is_reported_not_silently_skipped(self):
        self._install()
        result = self.db.verify_package("demo", strict=True)
        self.assertEqual(result["generated"], ["var/cache/demo/index"])

    def test_an_absent_generated_file_still_reports_missing(self):
        """Existence is checked. The exemption is content only."""
        self._install()
        (self.root / "var/cache/demo/index").unlink()
        result = self.db.verify_package("demo", strict=True)
        self.assertIn("var/cache/demo/index", result["missing"])
        self.assertEqual(result["generated"], [])

    def test_payload_content_is_still_checked(self):
        """The exemption must not leak onto the archive-deployed payload."""
        self._install()
        (self.root / "usr/bin/demo").write_text("tampered\n")
        result = self.db.verify_package("demo", strict=True)
        self.assertIn("usr/bin/demo", result["modified"])


class ManifestAndImportTests(_InstallHarness):

    HOOK = ('mkdir -p "$PKM_PACKAGE_ROOT/var/cache/demo"\n'
            'printf generated > "$PKM_PACKAGE_ROOT/var/cache/demo/index"\n')

    def _install(self):
        ok, msg = self.installer.install(
            "demo", archive_path=str(self._archive(hook_body=self.HOOK)))
        self.assertTrue(ok, msg)
        return self.root / "var/lib/igos/packages/demo-1.0"

    def test_the_manifest_carries_the_generated_paths(self):
        manifest = self._install()
        body = manifest.read_text()
        self.assertIn("var/cache/demo/index", body)
        self.assertIn("var/cache/demo/", body)

    def test_the_manifest_hash_stamp_matches_the_rewritten_bytes(self):
        manifest = self._install()
        row = self.db.get_installed("demo")
        self.assertEqual(
            row["manifest_sha256"], _sha256(str(manifest)),
            "the manifest was re-emitted but the row still stamps the "
            "pre-hook bytes, so every import re-registers this package")

    def test_import_keeps_the_generated_flag(self):
        """A text manifest cannot state generated-ness; the row must keep it.

        `pkm import` cascades the old file rows away and rebuilds them from
        the manifest, and it runs corpus-wide after every bash-tier package
        build. Without the carry, one import demotes every hook-generated
        file to a normal owned file and the next verify calls a healthy
        machine modified.
        """
        self._install()
        self.assertEqual(self._owned("var/cache/demo/index"), (1,))

        rows = self._force_reregister()
        self.assertEqual(
            rows, 1, "the import skipped — this test proves nothing unless a "
                     "re-register actually ran")
        self.assertEqual(
            self._owned("var/cache/demo/index"), (1,),
            "the re-register demoted a hook-generated file to a plain one")

    def test_import_does_not_invent_generated_rows(self):
        self._install()
        self.assertEqual(self._force_reregister(), 1)
        self.assertEqual(self._owned("usr/bin/demo"), (0,))

    def _force_reregister(self):
        """Make the next import actually re-register this package.

        Component A keys re-registration on the stored manifest hash, and the
        install path stamps it — so an import straight after an install is a
        no-op and would let a broken carry pass. NULLing the stamp is the
        state every row on a pre-Component-A substrate DB is already in, and
        it is what makes the corpus-wide import after a bash-tier build
        rebuild these rows.
        """
        self.db.conn.execute(
            "UPDATE installed SET manifest_sha256 = NULL WHERE name = 'demo'")
        self.db.conn.commit()
        return self.db.import_manifests(
            manifest_dir=self.root / "var/lib/igos/packages")


class SealRoundTripTests(_InstallHarness):
    """recipe function -> sealed .scripts -> pkm installs -> output owned.

    The end-to-end case these tests exist for, taken from the recipe text rather
    than from a hand-written hook script, so the seam and the recorder are
    proven against each other and not each against its own fixture.
    """

    def test_recipe_post_install_output_is_registered_after_install(self):
        import hookseal

        stg = self.tmp / "stg-round"
        _touch(stg / "usr/bin/round", "payload\n")
        (stg / ".PKGINFO").write_text("pkgname = round\npkgver = 2.0\n")

        build_sh = self.tmp / "build.sh"
        build_sh.write_text(
            "build() {\n    make\n}\n"
            "post_install() {\n"
            "    set -e\n"
            '    mkdir -p "$PKM_PACKAGE_ROOT/var/cache/round"\n'
            '    printf idx > "$PKM_PACKAGE_ROOT/var/cache/round/db"\n'
            "}\n")
        self.assertEqual(
            hookseal.seal_into_staging(stg, build_sh, "round", "2.0"),
            ["post_install"])

        arc = self.tmp / "round-2.0.igos.tar.gz"
        with tarfile.open(arc, "w:gz") as tf:
            tf.add(stg, arcname=".")

        ok, msg = self.installer.install("round", archive_path=str(arc))
        self.assertTrue(ok, msg)

        self.assertTrue((self.root / "var/cache/round/db").is_file(),
                        "the sealed recipe function did not run on install")
        self.assertEqual(self._owned("var/cache/round/db"), (1,),
                         "it ran, and what it wrote is owned by nobody")
        self.assertFalse((self.root / ".scripts").exists(),
                         "the sealed hook itself must never deploy")


if __name__ == "__main__":
    unittest.main()
