#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A replaced Python module never runs from stale bytecode, wherever the
interpreter keeps its cache.

THE GAP. The deploy drops the replaced module's compiled copy from its
directory's __pycache__ before the archive lands (tests/pkm/
test_stale_bytecode_purge.py). An interpreter can keep its cache somewhere
else: PYTHONPYCACHEPREFIX or `-X pycache_prefix` move every compiled file into
a separate tree, and the purge never looks there. CPython accepts a cached
copy when the source's recorded modification time (whole seconds) and size
still match, and a deployed file's time comes out of the archive, so an
upgrade whose new build keeps the old size and time left such an interpreter
running the code the upgrade had just replaced. Measured with a real
interpreter against the tree before this change: after the upgrade the module
answered OLD.

THE FIX ASSERTED HERE. The deploy gives each Python source it writes the
deploy time as its modification time, so a cached copy built against the file
it replaced no longer matches, in __pycache__ or under any prefix. The purge
stays. A module whose archive ships its own compiled copy keeps the archive's
time: that copy is timestamp-checked against it, and a new time would make a
root import rewrite a file pkm tracks and verifies by content.

The first case is the one that matters most: the whole path as a machine runs
it - an install, a run under a prefix, the upgrade the way `pkm upgrade` does
it (remove, then install), and a run under the same prefix.
"""

import io
import os
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

# The archive time both builds carry: the collision a deploy can present.
ARCHIVE_MTIME = 1_786_000_000
MODULE_DIR = "usr/lib/stalepy"


def _pkginfo(version, release, filecount):
    lines = [
        "pkgname=stalepy", f"pkgver={version}", f"pkgrel={release}",
        "pkgdesc=stale-bytecode test package", "license=GPL", "tier=core",
        "builddate=2026-09-29T00:00:00Z", "size=64", f"filecount={filecount}",
    ]
    return ("\n".join(lines) + "\n").encode()


def _add(tf, name, data, mtime=ARCHIVE_MTIME, mode=0o644):
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    ti.mtime = mtime
    ti.mode = mode
    tf.addfile(ti, io.BytesIO(data))


def _archive(tmp, version, answer, extra=()):
    """A package archive holding usr/lib/stalepy/greet.py, which answers
    `answer`. Every build carries the same member time and, for OLD and NEW,
    the same size: the condition under which CPython accepts a stale cache."""
    path = Path(tmp) / f"stalepy-{version}-1.igos.tar.gz"
    source = f"def answer():\n    return {answer!r}\n".encode()
    with tarfile.open(path, "w:gz") as tf:
        _add(tf, "./.PKGINFO", _pkginfo(version, 1, 1 + len(extra)))
        _add(tf, f"./{MODULE_DIR}/greet.py", source)
        for name, data in extra:
            _add(tf, f"./{MODULE_DIR}/{name}", data)
    return path


def _target(tmp):
    target = Path(tmp) / "target"
    (target / "etc").mkdir(parents=True)
    (target / "etc" / "passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
    (target / "etc" / "group").write_text("root:x:0:\n")
    return target


class _Machine:
    """A scratch root with pkm's own database, installer and remover."""

    def __init__(self, tmp):
        import importlib
        rootpaths = importlib.import_module("pkm.rootpaths")
        from pkm.database import PackageDB
        from pkm.installer import PackageInstaller
        from pkm.remover import PackageRemover
        self.tmp = Path(tmp)
        self.target = _target(tmp)
        self.db = PackageDB(str(rootpaths.db_path(self.target)), root=str(self.target))
        self.installer = PackageInstaller(self.db, root=str(self.target))
        self.remover = PackageRemover(self.db, root=str(self.target))
        self.driver = self.tmp / "run.py"
        self.driver.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(self.target / MODULE_DIR)!r})\n"
            "import greet\n"
            "print(greet.answer())\n"
        )

    def close(self):
        self.db.close()

    def install(self, archive):
        ok, msg = self.installer.install("stalepy", archive_path=str(archive))
        if not ok:
            raise AssertionError(f"install failed: {msg}")

    def upgrade(self, archive):
        """The upgrade the way `pkm upgrade` performs it: the installed
        version removed without its pre-remove hook, then the new one
        installed."""
        ok, msg = self.remover.remove("stalepy", force=True,
                                      run_pre_remove_hook=False)
        if not ok:
            raise AssertionError(f"remove before the upgrade failed: {msg}")
        self.install(archive)

    def run(self, prefix=None):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.tmp)}
        if prefix is not None:
            env["PYTHONPYCACHEPREFIX"] = str(prefix)
        res = subprocess.run([sys.executable, str(self.driver)],
                             capture_output=True, text=True, env=env)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        return res.stdout.strip()

    def module(self):
        return self.target / MODULE_DIR / "greet.py"


def _after_this_second():
    """Wait until the wall clock has entered the next second. CPython's
    timestamp check compares whole seconds; two deploys of one path inside the
    same second cannot be told apart by time, and a case about two deploys
    must not depend on the scheduler to separate them."""
    time.sleep(1.01 - (time.time() % 1.0))


class AnUpgradeUnderACachePrefixRunsTheNewCode(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.old = _archive(self.tmp, "1.0", "OLD")
        self.new = _archive(self.tmp, "1.1", "NEW")
        self.m = _Machine(self.tmp)
        self.prefix = self.tmp / "pycache-prefix"

    def tearDown(self):
        self.m.close()
        self._td.cleanup()

    def test_an_upgrade_over_a_file_with_the_archive_time_runs_the_new_code(self):
        """Every machine this change reaches carries files an earlier pkm
        deployed with the archive's time. The first upgrade after it must
        not leave a prefixed interpreter on the old code."""
        self.m.install(self.old)
        os.utime(self.m.module(), (ARCHIVE_MTIME, ARCHIVE_MTIME))
        self.assertEqual(self.m.run(prefix=self.prefix), "OLD")
        self.m.upgrade(self.new)
        self.assertEqual(
            self.m.run(prefix=self.prefix), "NEW",
            "after the upgrade an interpreter with a cache prefix still runs "
            "the bytecode compiled from the replaced module")

    def test_two_upgrades_a_second_apart_each_run_the_new_code(self):
        self.m.install(self.old)
        self.assertEqual(self.m.run(prefix=self.prefix), "OLD")
        _after_this_second()
        self.m.upgrade(self.new)
        self.assertEqual(self.m.run(prefix=self.prefix), "NEW")

    def test_without_a_prefix_the_upgrade_runs_the_new_code(self):
        """The __pycache__ side, which the purge already closes: kept here
        so the stamp is never the only thing standing between the two."""
        self.m.install(self.old)
        os.utime(self.m.module(), (ARCHIVE_MTIME, ARCHIVE_MTIME))
        self.assertEqual(self.m.run(), "OLD")
        self.m.upgrade(self.new)
        self.assertEqual(self.m.run(), "NEW")


class TheStampLeavesEverythingElseAsTheArchiveMadeIt(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.m = _Machine(self.tmp)

    def tearDown(self):
        self.m.close()
        self._td.cleanup()

    def test_a_deployed_source_carries_the_deploy_time(self):
        before = int(time.time())
        self.m.install(_archive(self.tmp, "1.0", "OLD"))
        mtime = int(self.m.module().stat().st_mtime)
        self.assertGreaterEqual(mtime, before,
                                "the deployed source still carries the archive's time")

    def test_a_file_that_is_not_python_source_keeps_the_archive_time(self):
        self.m.install(_archive(self.tmp, "1.0", "OLD",
                                extra=(("data.txt", b"payload\n"),)))
        data = self.m.target / MODULE_DIR / "data.txt"
        self.assertEqual(int(data.stat().st_mtime), ARCHIVE_MTIME)

    def test_verify_finds_nothing_modified_after_the_stamp(self):
        from pkm.verifier import PackageVerifier
        self.m.install(_archive(self.tmp, "1.0", "OLD"))
        self.m.upgrade(_archive(self.tmp, "1.1", "NEW"))
        result = PackageVerifier(self.m.db).verify("stalepy", mode="strict")
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["modified"], [])

    def test_a_module_shipped_with_its_compiled_copy_keeps_the_archive_time(self):
        """The archive's own compiled copy is timestamp-checked against the
        source's time. A new time would make it stale: a root import then
        rewrites a file pkm tracks and verifies by content."""
        import py_compile
        src = self.tmp / "build" / MODULE_DIR / "greet.py"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"def answer():\n    return 'OLD'\n")
        os.utime(src, (ARCHIVE_MTIME, ARCHIVE_MTIME))
        pyc = Path(py_compile.compile(str(src), doraise=True))
        archive = self.tmp / "stalepy-1.0-1.igos.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            _add(tf, "./.PKGINFO", _pkginfo("1.0", 1, 2))
            _add(tf, f"./{MODULE_DIR}/greet.py", src.read_bytes())
            _add(tf, f"./{MODULE_DIR}/__pycache__/{pyc.name}", pyc.read_bytes())
        self.m.install(archive)
        deployed = self.m.module()
        self.assertEqual(int(deployed.stat().st_mtime), ARCHIVE_MTIME,
                         "the source of a module shipped with its compiled copy "
                         "was given a new time")
        shipped = self.m.target / MODULE_DIR / "__pycache__" / pyc.name
        recorded_mtime, recorded_size = struct.unpack("<II", shipped.read_bytes()[8:16])
        self.assertEqual((recorded_mtime, recorded_size),
                         (ARCHIVE_MTIME, deployed.stat().st_size))
        before = shipped.read_bytes()
        self.assertEqual(self.m.run(), "OLD")
        self.assertEqual(shipped.read_bytes(), before,
                         "an import rewrote the compiled copy the archive shipped")


class TheStampSitsBetweenTheDeployAndTheHook(unittest.TestCase):
    """ORDER IS LOAD-BEARING. After the deploy extract, or the archive's time
    overwrites the stamp; before the hook's snapshot, or the hook records see
    the stamp's ctime change as the hook's own write."""

    def test_the_stamp_is_called_after_the_extract_and_before_the_hook_snapshot(self):
        from pkm import installer as installer_mod
        src = Path(installer_mod.__file__).read_text(encoding="utf-8")
        deploy_marker = "ok, err = _safe_extract_tar(\n                archive_path, self.root, exclude_paths=deploy_excludes,"
        self.assertIn(deploy_marker, src)
        after_deploy = src.split(deploy_marker, 1)[1]
        stamp = "_stamp_deployed_python_sources("
        self.assertIn(stamp, after_deploy, "no stamp after the deploy extract")
        snapshot = "fs_snapshot(self.root) if hook_present else None"
        self.assertIn(snapshot, after_deploy)
        self.assertLess(after_deploy.index(stamp), after_deploy.index(snapshot),
                        "the stamp runs after the hook's snapshot")


if __name__ == "__main__":
    unittest.main()
