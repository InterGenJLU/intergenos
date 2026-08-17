# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The two registration paths must record the SAME ownership rows.

A package can enter the pkm files table two ways: the tracked-deploy path
(igos-build stages a tree, writes the manifest, then registers it) and the
archive-install path (``pkm install`` walks the extracted staging tree). The
archive walk records a row for every directory (``is_dir=1``); the tracked
path recorded FILES ONLY, so every manifest-declared directory of a
tracked-deployed package was unowned in the DB. An EMPTY directory has no
file underneath it to imply ownership, so it went unowned outright and the
squashfs ownership gate reported it as an unowned path — re-registering the
package through the archive path healed the symptom because that path walks
directories.

These tests pin the parity itself rather than the symptom: register one
fixture package through BOTH paths and compare the row sets. They fail on the
files-only registration and pass once the tracked path carries directories.
"""

from __future__ import annotations

import importlib
import os
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace

# Import the LIVE modules from the worktree this test file lives in — never a
# hardcoded absolute root (a hardcoded root silently tests the wrong tree
# when running from a second worktree).
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from pkm.database import PackageDB  # noqa: E402
from pkm.installer import PackageInstaller  # noqa: E402

from .factories import make_package  # noqa: E402

_tracker_mod = importlib.import_module("igos-build.tracker")
PackageTracker = _tracker_mod.PackageTracker

# The fixture tree. `var/lib/demo` is deliberately EMPTY — it is the shape the
# defect made invisible, since no file row underneath it implies ownership.
PAYLOAD = {
    "usr/bin/demo": "#!/bin/sh\nexit 0\n",
    "usr/share/demo/data.txt": "payload\n",
    "etc/demo/demo.conf": "key = value\n",
}
EMPTY_DIRS = ["var/lib/demo", "usr/share/demo/themes"]


class _Logger:
    def __init__(self):
        self.errors, self.infos = [], []

    def error(self, msg):
        self.errors.append(msg)

    def info(self, msg):
        self.infos.append(msg)


def _rows(db, name):
    """The ownership row set as the comparison sees it: path + both flags."""
    pkg = db.get_installed(name)
    cur = db.conn.execute(
        "SELECT path, is_dir, is_config FROM files WHERE package_id = ?",
        (pkg["id"],),
    )
    return {(r[0], bool(r[1]), bool(r[2])) for r in cur.fetchall()}


class _TrackerHarness:
    """Drive the real tracker methods with a stub host class.

    PackageTracker is a mixin: it reads self.logger / self.pkg_db and calls its
    own helpers. Binding the REAL methods to a namespace exercises the code
    under test without standing up a BuildExecutor.
    """

    def __init__(self, tmp: Path, db_path: Path, root: Path):
        self.stub = SimpleNamespace(logger=_Logger(), pkg_db=tmp / "pkg_db")
        self.stub.pkg_db.mkdir(parents=True, exist_ok=True)
        # Bind EVERY PackageTracker method onto the stub rather than a
        # hand-maintained list. The list form is silent drift by construction —
        # the exact failure factories.py exists to prevent, one layer up: adding
        # a method to PackageTracker that pkg_manifest calls broke this double
        # with an AttributeError that said nothing about the change. A derived
        # bind cannot fall behind the class.
        for meth, fn in vars(PackageTracker).items():
            if callable(fn) and not isinstance(fn, (staticmethod, classmethod)):
                setattr(self.stub, meth, MethodType(fn, self.stub))
        self.stub._compute_file_hashes = PackageTracker._compute_file_hashes
        self.stub.diff_snapshots = PackageTracker.diff_snapshots
        self.stub._parse_manifest_paths = PackageTracker._parse_manifest_paths
        self.stub.SNAPSHOT_PRUNE_DEFAULT = PackageTracker.SNAPSHOT_PRUNE_DEFAULT
        self._db_path, self._root = db_path, root

    def __enter__(self):
        # _write_pkm_db opens PackageDB() with production defaults; point that
        # one construction at the test DB for the duration of the call.
        self._saved = _tracker_mod.PackageDB
        db_path, root = self._db_path, self._root
        _tracker_mod.PackageDB = lambda: PackageDB(db_path, root=str(root))
        return self.stub

    def __exit__(self, *exc):
        _tracker_mod.PackageDB = self._saved
        return False


def _make_staging(tmp: Path) -> Path:
    staging = tmp / "staging"
    for rel, text in PAYLOAD.items():
        p = staging / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for rel in EMPTY_DIRS:
        (staging / rel).mkdir(parents=True, exist_ok=True)
    return staging


def _make_pkg(tmp: Path, name="demo"):
    d = tmp / "packages" / "core" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "package.yml").write_text(f"name: {name}\n")
    return make_package(name=name, version="1.0", template_path=d / "package.yml")


class TrackedDeployDirRowParityTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pkg = _make_pkg(self.tmp)
        self.staging = _make_staging(self.tmp)

    def _register_tracked(self) -> set:
        """Path A — igos-build stages, manifests, then registers."""
        db_path = self.tmp / "tracked.db"
        root = self.tmp / "tracked-root"
        root.mkdir(exist_ok=True)
        with _TrackerHarness(self.tmp, db_path, root) as stub:
            self.assertTrue(
                PackageTracker.pkg_manifest(stub, self.pkg, self.staging),
                f"pkg_manifest failed: {stub.logger.errors}")
            self.assertTrue(
                PackageTracker.pkg_register_pkm_db(stub, self.pkg),
                f"pkg_register_pkm_db failed: {stub.logger.errors}")
        db = PackageDB(db_path, root=str(root))
        try:
            return _rows(db, self.pkg.name)
        finally:
            db.close()

    def _register_archive(self) -> set:
        """Path B — the same staged tree, packed and installed by pkm."""
        arc = self.tmp / f"{self.pkg.name}-{self.pkg.version}.igos.tar.gz"
        # The real archiver runs `tar -C <staging> -czf <archive> .`, which
        # stores every member `./`-prefixed; tarfile's arcname="." mirrors it.
        with tarfile.open(arc, "w:gz") as tf:
            tf.add(self.staging, arcname=".")
        db_path = self.tmp / "archive.db"
        root = self.tmp / "archive-root"
        root.mkdir(exist_ok=True)
        db = PackageDB(db_path, root=str(root))
        try:
            ok, msg = PackageInstaller(db, root=str(root)).install(
                self.pkg.name, archive_path=arc)
            self.assertTrue(ok, f"archive install failed: {msg}")
            return _rows(db, self.pkg.name)
        finally:
            db.close()

    def test_archive_path_owns_its_directories(self):
        """Guard on the reference side: the comparison is only meaningful if
        the archive path really does record directory rows."""
        rows = self._register_archive()
        dirs = {p for p, is_dir, _ in rows if is_dir}
        self.assertIn("var/lib/demo", dirs)
        self.assertIn("usr/share/demo", dirs)

    def test_tracked_deploy_owns_its_directories(self):
        rows = self._register_tracked()
        dirs = {p for p, is_dir, _ in rows if is_dir}
        self.assertTrue(
            dirs, "tracked deploy registered ZERO directory rows — every "
                  "manifest-declared directory is unowned in the DB")
        self.assertIn(
            "var/lib/demo", dirs,
            "an EMPTY manifest directory has no file row to imply ownership; "
            "it must carry its own is_dir row or it is unowned outright")

    def test_both_paths_record_identical_row_sets(self):
        tracked = self._register_tracked()
        archive = self._register_archive()
        self.assertEqual(
            tracked, archive,
            "the same package registered through the tracked-deploy path and "
            "the archive-install path must yield identical ownership rows "
            "(path, is_dir, is_config)")

    def test_directory_paths_carry_no_trailing_slash(self):
        # The trailing "/" is the marker the writer reads; it must not survive
        # into the stored path, or every consumer's path comparison misses.
        for row_set in (self._register_tracked(), self._register_archive()):
            for path, _is_dir, _is_config in row_set:
                self.assertFalse(path.endswith("/"),
                                 f"stored path {path!r} kept its trailing slash")

    def test_config_semantics_unchanged_by_the_dir_rows(self):
        rows = self._register_tracked()
        by_path = {p: (d, c) for p, d, c in rows}
        self.assertEqual(by_path["etc/demo/demo.conf"], (False, True),
                         "a file under etc/ stays a config file")
        self.assertEqual(by_path["etc/demo"], (True, False),
                         "a DIRECTORY under etc/ is not a config file")


class DirectInstallDirRowTests(unittest.TestCase):
    """The filesystem-diff lane registers through the same gate, so the parent
    directories its manifest declares must reach the files table too."""

    def test_diff_manifest_directories_are_registered(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pkg = _make_pkg(tmp, name="direct-demo")
            newfile = tmp / "opt" / "direct-demo" / "bin" / "tool"
            newfile.parent.mkdir(parents=True)
            newfile.write_text("payload\n")

            def snap(p):
                st = os.lstat(p)
                return (st.st_size, st.st_mtime_ns, st.st_ctime_ns)

            build_start = time.time()
            before: dict = {}
            after = {str(newfile): snap(newfile)}

            db_path, root = tmp / "direct.db", tmp / "direct-root"
            root.mkdir()
            with _TrackerHarness(tmp, db_path, root) as stub:
                self.assertTrue(
                    PackageTracker.pkg_manifest_from_diff(
                        stub, pkg, before, after,
                        build_start_time=build_start - 1),
                    f"pkg_manifest_from_diff failed: {stub.logger.errors}")
                self.assertTrue(
                    PackageTracker.pkg_register_pkm_db(stub, pkg),
                    f"pkg_register_pkm_db failed: {stub.logger.errors}")

            db = PackageDB(db_path, root=str(root))
            try:
                rows = _rows(db, pkg.name)
            finally:
                db.close()
            manifest = (stub.pkg_db / f"{pkg.name}-{pkg.version}").read_text()
            declared = {ln.rstrip("/") for ln in manifest.splitlines()
                        if ln.endswith("/")}
            self.assertTrue(declared, "fixture must declare parent directories")
            self.assertLessEqual(
                declared, {p for p, is_dir, _ in rows if is_dir},
                "every directory the diff manifest declares must be owned")


if __name__ == "__main__":
    unittest.main()
