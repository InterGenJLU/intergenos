"""Review finding H3 regression tests: ctime-snapshot diff for direct_install tracking.

Closes the two residual direct_install gaps the old net-new-path-set diff left
open:

  1. Content overwrites of PRE-EXISTING files were invisible: the path is in
     both the before and after snapshot, so a path-set diff cancels it, and the
     only rescue (the supersedee check) keyed on mtime — which cp -a / tar -p /
     touch -r preserve, i.e. it is forgeable. ctime is bumped by the kernel on
     every content/metadata write and cannot be set from userland, so a ctime
     delta catches the overwrite even when mtime is forged back.
  2. Writes outside the former six walked roots (/usr /etc /opt /var/lib /lib
     /boot) were never seen. The expanded "/" walk observes writes anywhere
     except the explicit, logged prune list.

ctime here is POSIX inode-change-time — these tests assume a POSIX host.
"""

import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace

# Import the LIVE module from the worktree this test file lives in (never a
# hardcoded absolute root — that silently tests the wrong tree from a second
# worktree).
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from .factories import make_package, make_tracker_stub  # noqa: E402
_tracker_mod = importlib.import_module("igos-build.tracker")
PackageTracker = _tracker_mod.PackageTracker


class _Logger:
    def __init__(self):
        self.errors, self.infos = [], []

    def error(self, msg):
        self.errors.append(msg)

    def info(self, msg):
        self.infos.append(msg)

    def warning(self, msg):
        self.infos.append(msg)


def _snap_stub():
    """Stub for fs_snapshot + diff_snapshots (binds the whole class)."""
    return make_tracker_stub(logger=_Logger())


def _mk_manifest_stub(tmp: Path):
    """Fuller stub able to run pkg_manifest_from_diff end to end."""
    stub = make_tracker_stub(logger=_Logger(), pkg_db=tmp / "pkg_db")
    stub.pkg_db.mkdir(parents=True, exist_ok=True)
    return stub


def _mk_pkg(tmp: Path, name="demo", supersedes=()):
    d = tmp / "packages" / "extra" / name
    d.mkdir(parents=True)
    (d / "package.yml").write_text(f"name: {name}\n")
    return make_package(
        name=name, description="demo pkg",
        supersedes=list(supersedes), template_path=d / "package.yml",
    )


def _snap(stub, root):
    # Walk the fixture root with NO prune (pass empty to be explicit).
    return stub.fs_snapshot(dirs=[str(root)], prune=set())


class TestCtimeSnapshotDiff(unittest.TestCase):

    def test_forged_mtime_overwrite_is_caught_by_ctime(self):
        """A content overwrite that forges mtime back (the cp -a / tar -p
        replay) is INVISIBLE to an mtime check but caught by the ctime delta."""
        stub = _snap_stub()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tool = root / "usr" / "bin" / "tool"
            tool.parent.mkdir(parents=True)
            tool.write_text("original\n")

            before = _snap(stub, root)
            orig_mtime_ns = before[str(tool)][1]

            # Overwrite content, then FORGE mtime back to the original — exactly
            # what cp -a / tar -p / touch -r do. ctime is bumped to now by both
            # the write and the utime and cannot be restored from userland.
            time.sleep(0.01)
            tool.write_text("EVIL PAYLOAD\n")
            os.utime(tool, ns=(orig_mtime_ns, orig_mtime_ns))

            after = _snap(stub, root)

            # The forgery succeeded at the mtime layer, so an mtime-only diff
            # would MISS the overwrite:
            self.assertEqual(
                before[str(tool)][1], after[str(tool)][1],
                "test setup: mtime must be forged back to fool an mtime check")
            # ctime caught it:
            self.assertNotEqual(
                before[str(tool)][2], after[str(tool)][2],
                "ctime must change on the overwrite")
            created, modified = stub.diff_snapshots(before, after)
            self.assertIn(str(tool), modified,
                          "ctime delta must flag the forged-mtime overwrite")
            self.assertEqual(created, set(),
                             "no net-new path in a pure-overwrite scenario")

    def test_overwritten_file_enters_manifest(self):
        """End to end: a forged-mtime overwrite of a pre-existing file is
        CLAIMED in the generated manifest (a MEASURED overwrite), alongside a
        genuinely new file."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            stub = _mk_manifest_stub(tmp)
            root = tmp / "root"
            tool = root / "usr" / "bin" / "tool"
            tool.parent.mkdir(parents=True)
            tool.write_text("original\n")
            newf = root / "usr" / "bin" / "brandnew"

            before = stub.fs_snapshot(dirs=[str(root)], prune=set())
            orig_mtime_ns = before[str(tool)][1]

            build_start = time.time()
            time.sleep(0.01)
            tool.write_text("EVIL\n")                       # overwrite content
            os.utime(tool, ns=(orig_mtime_ns, orig_mtime_ns))  # forge mtime back
            newf.write_text("new\n")                        # genuine net-new
            after = stub.fs_snapshot(dirs=[str(root)], prune=set())

            pkg = _mk_pkg(tmp)
            ok = PackageTracker.pkg_manifest_from_diff(
                stub, pkg, before, after, build_start_time=build_start)
            self.assertTrue(ok, f"manifest failed: {stub.logger.errors}")
            manifest = (stub.pkg_db / "demo-1.0").read_text()
            self.assertIn("usr/bin/brandnew", manifest,
                          "the genuinely new file must be claimed")
            self.assertIn(
                "usr/bin/tool", manifest,
                "the forged-mtime overwrite of a pre-existing file must be "
                "claimed as a measured overwrite")

    def test_write_outside_former_roots_is_caught(self):
        """A write outside the former six walked roots (/usr /etc /opt /var/lib
        /lib /boot) — here under /srv — is now observed by the expanded walk."""
        stub = _snap_stub()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Seed a file under one of the OLD roots so `before` is non-empty.
            (root / "usr" / "bin").mkdir(parents=True)
            (root / "usr" / "bin" / "pre").write_text("pre\n")
            before = _snap(stub, root)

            # Package writes under /srv — invisible to the OLD 6-root walk.
            srv = root / "srv" / "app" / "data"
            srv.parent.mkdir(parents=True)
            srv.write_text("payload\n")
            after = _snap(stub, root)

            created, modified = stub.diff_snapshots(before, after)
            self.assertIn(
                str(srv), created,
                "a write outside the former roots must be caught by the "
                "expanded walk")

    def test_noop_build_stays_clean(self):
        """Two snapshots with nothing changed between them yield an empty
        diff — no false overwrites from the ctime check."""
        stub = _snap_stub()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "usr" / "lib").mkdir(parents=True)
            (root / "usr" / "lib" / "a.so").write_text("a\n")
            (root / "etc").mkdir(parents=True)
            (root / "etc" / "conf").write_text("c\n")

            before = _snap(stub, root)
            # ... build does nothing ...
            after = _snap(stub, root)

            created, modified = stub.diff_snapshots(before, after)
            self.assertEqual(created, set(), "a no-op build creates nothing")
            self.assertEqual(modified, set(), "a no-op build modifies nothing")

    def test_prune_list_is_honored_and_logged(self):
        """A pruned subtree is not walked, and the effective prune list is
        logged (the 'explicit, LOGGED prune list' requirement)."""
        stub = _snap_stub()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            keep = root / "usr" / "bin" / "keep"
            keep.parent.mkdir(parents=True)
            keep.write_text("keep\n")
            # A build-scratch intermediate under a pruned subtree.
            scratch = root / "scratch" / "obj.o"
            scratch.parent.mkdir(parents=True)
            scratch.write_text("intermediate\n")

            snap = stub.fs_snapshot(dirs=[str(root)],
                                    prune={str(root / "scratch")})
            self.assertIn(str(keep), snap)
            self.assertNotIn(str(scratch), snap,
                             "a pruned subtree must not be walked")
            self.assertTrue(
                any("fs_snapshot: walk=" in m and "prune=" in m
                    for m in stub.logger.infos),
                "the effective prune list must be logged")


if __name__ == "__main__":
    unittest.main()
