"""Regression tests: archive tar-recursion contamination (2026-07-20).

The direct_install archive path (pkg_archive_from_files) tars `-C / -T
<filelist>`. GNU tar archives a DIRECTORY named in -T recursively by default,
so a directory path in the file list (a created dir, or a supersedee manifest
dir row flagged by _detect_overwrites) swept the directory's entire live
subtree into the archive — shared dirs dragged every other package's files
along (dbus-pass2 sealed at 2.5 GB carrying all of /usr/bin and /run).

Three guards under test:
  1. --no-recursion: a dir in the file list archives as a metadata-only entry.
  2. The member-count gate: sealed tar member count must equal the claimed
     list + ./.PKGINFO; a mismatch fails the seal and quarantines (.failed).
  3. _detect_overwrites never returns directories — a dir's mtime bumps when
     ANY entry inside changes, so supersedee dir rows always looked rewritten.
"""

import importlib
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest import mock

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


def _mk_archive_stub(tmp: Path):
    # make_tracker_stub binds the WHOLE class, so a method added to
    # PackageTracker arrives here without this file being touched. The two
    # lambdas below are deliberate behaviour stubs, not drift management.
    stub = make_tracker_stub(logger=_Logger(), pkg_archives=tmp / "archives")
    stub.pkg_archives.mkdir(parents=True, exist_ok=True)
    stub._build_pkginfo = lambda pkg, size, count: (
        f"pkgname = {pkg.name}\npkgver = {pkg.version}\n")
    stub._enforce_mirror_archive_verify_paths = lambda pkg, path: True
    return stub


def _mk_pkg(name="demo"):
    return make_package(name=name)


class ArchiveNoRecursionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        # The function under test resolves list paths against -C /; build the
        # payload in the real filesystem under the tempdir and hand absolute
        # paths in (lstripped to relative by the function itself).
        self.payload = self.tmp / "payload"
        (self.payload / "shared.d").mkdir(parents=True)
        (self.payload / "shared.d" / "mine.conf").write_text("mine")
        (self.payload / "shared.d" / "other-packages.conf").write_text("not mine")
        (self.payload / "mybin").write_text("#!/bin/sh\n")
        # Route traced_run through plain subprocess so the test does not
        # depend on trace-root setup.
        self._trace_mod = importlib.import_module("igos-build._trace")
        self._orig_traced_run = self._trace_mod.traced_run
        self._trace_mod.traced_run = lambda cmd, **kw: subprocess.run(
            cmd, capture_output=True, text=True)

    def tearDown(self):
        self._trace_mod.traced_run = self._orig_traced_run
        self._tmp.cleanup()

    def _members(self, archive: Path):
        with tarfile.open(archive, "r:gz") as t:
            return sorted(t.getnames())

    def test_directory_in_list_does_not_recurse(self):
        """A dir in new_files archives as a metadata entry — no children."""
        stub = _mk_archive_stub(self.tmp)
        pkg = _mk_pkg()
        new_files = [str(self.payload / "shared.d"),
                     str(self.payload / "shared.d" / "mine.conf"),
                     str(self.payload / "mybin")]
        self.assertTrue(stub.pkg_archive_from_files(pkg, new_files),
                        msg=f"errors: {stub.logger.errors}")
        archive = stub.pkg_archives / "demo-1.0.igos.tar.gz"
        members = self._members(archive)
        rel = str(self.payload).lstrip("/")
        self.assertIn(f"{rel}/shared.d", members)
        self.assertIn(f"{rel}/shared.d/mine.conf", members)
        self.assertNotIn(
            f"{rel}/shared.d/other-packages.conf", members,
            "tar recursed a directory: unclaimed sibling content was sealed "
            "into the archive (the 2026-07-20 contamination class)")
        # Exactly the claimed set + ./.PKGINFO.
        self.assertEqual(len(members), len(new_files) + 1)

    def test_member_count_gate_quarantines_on_mismatch(self):
        """The gate is the backstop: if recursion regresses, the seal fails.

        Simulate the exact historical regression by stripping --no-recursion
        from the tar command at run time; the dir in the list then recurses
        (extra unclaimed members) and the member-count gate must refuse the
        seal and quarantine.
        """
        self._trace_mod.traced_run = lambda cmd, **kw: subprocess.run(
            [a for a in cmd if a != "--no-recursion"],
            capture_output=True, text=True)
        stub = _mk_archive_stub(self.tmp)
        pkg = _mk_pkg()
        new_files = [str(self.payload / "shared.d"),
                     str(self.payload / "mybin")]
        ok = stub.pkg_archive_from_files(pkg, new_files)
        self.assertFalse(ok, "member-count mismatch must fail the seal")
        self.assertTrue(any("member-count gate FAILED" in e
                            for e in stub.logger.errors), stub.logger.errors)
        self.assertFalse(
            (stub.pkg_archives / "demo-1.0.igos.tar.gz").exists(),
            "a refused seal must not leave the archive in place")
        self.assertTrue(
            (stub.pkg_archives / "demo-1.0.igos.tar.gz.failed").exists(),
            "a refused seal must quarantine as .failed")

    def test_detect_overwrites_skips_directories(self):
        """Supersedee dir rows must never enter the overwrite set."""
        stub = SimpleNamespace(logger=_Logger())
        stub._detect_overwrites = MethodType(
            PackageTracker._detect_overwrites, stub)
        fresh_file = self.payload / "mybin"
        fresh_dir = self.payload / "shared.d"
        now = time.time()
        os.utime(fresh_file, (now, now))
        os.utime(fresh_dir, (now, now))
        stub._get_supersedee_paths = lambda pkg: {str(fresh_file),
                                                  str(fresh_dir)}
        # The fixtures live under /tmp, which the real prune set (correctly)
        # excludes — narrow it so this test exercises only the dir filter.
        with mock.patch.object(PackageTracker, "SNAPSHOT_PRUNE_DEFAULT",
                               frozenset({"/proc", "/sys"})):
            result = stub._detect_overwrites(_mk_pkg(), build_start_time=now - 60)
        self.assertIn(str(fresh_file), result)
        self.assertNotIn(
            str(fresh_dir), result,
            "a directory was flagged as overwritten payload — dir mtimes bump "
            "on any child change and must be excluded")

    def test_detect_overwrites_skips_never_payload_prune_trees(self):
        """Supersedee rows under the prune trees must never re-import.

        F23 second limb (live catch 2026-07-21): a supersedee manifest
        sealed under PRE-prune builder code still carried a
        /root/.cache/g-ir-scanner row; the rebuild regenerated the same
        content-addressed entry (fresh mtime) and _detect_overwrites fed
        the phantom back into the payload even though the fs-diff prune
        excludes /root. The prune set is the single definition of
        never-payload — the overwrite path must honor it too.
        """
        stub = SimpleNamespace(logger=_Logger())
        stub._detect_overwrites = MethodType(
            PackageTracker._detect_overwrites, stub)
        fresh_file = self.payload / "mybin"
        now = time.time()
        os.utime(fresh_file, (now, now))
        # Simulated historical phantom rows under two pruned trees; use
        # paths that EXIST on any build/test host so lexists+mtime would
        # pass if the prune filter were absent.
        stub._get_supersedee_paths = lambda pkg: {
            str(fresh_file),
            "/root/.cache/g-ir-scanner/deadbeef",
            "/mnt/intergenos/igos-build/__pycache__/x.pyc",
        }
        # Keep /root + /mnt/intergenos pruned (the trees under test) but
        # drop /tmp so the legit tempdir fixture survives the filter.
        with mock.patch.object(PackageTracker, "SNAPSHOT_PRUNE_DEFAULT",
                               frozenset({"/root", "/mnt/intergenos"})):
            result = stub._detect_overwrites(_mk_pkg(), build_start_time=now - 60)
        self.assertIn(str(fresh_file), result)
        self.assertNotIn("/root/.cache/g-ir-scanner/deadbeef", result,
                         "a pruned-tree supersedee row re-imported as payload")
        self.assertNotIn("/mnt/intergenos/igos-build/__pycache__/x.pyc", result,
                         "the in-chroot repo copy re-imported as payload")


class ArchiveSealedHookMemberCountTest(unittest.TestCase):
    """Regression: the member-count gate must count sealed lifecycle hooks.

    The tar command seals three member classes — the claimed payload,
    ./.PKGINFO, and the recipe's lifecycle hooks under ./.scripts/ — but the
    gate's arithmetic counted only the first two. The first direct_install
    package that sealed a hook (systemd-pass2, one post_install, ge9b-13
    attempt 2, 2026-08-05) therefore failed the gate deterministically on a
    CORRECT archive: 2330 members against an expectation of 2329. A gate that
    cannot count the archive's own designed contents converts the metadata
    contract into a build halt.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.payload = self.tmp / "payload"
        self.payload.mkdir(parents=True)
        (self.payload / "mybin").write_text("#!/bin/sh\n")
        (self.payload / "myconf").write_text("k=v\n")
        # A real recipe dir whose build.sh declares one lifecycle function, so
        # _seal_lifecycle_hooks seals it through the genuine hookseal path —
        # not a hand-planted .scripts file.
        self.recipe = self.tmp / "recipe"
        self.recipe.mkdir()
        (self.recipe / "build.sh").write_text(
            "post_install() {\n    ldconfig || true\n}\n")
        self._trace_mod = importlib.import_module("igos-build._trace")
        self._orig_traced_run = self._trace_mod.traced_run
        self._trace_mod.traced_run = lambda cmd, **kw: subprocess.run(
            cmd, capture_output=True, text=True)

    def tearDown(self):
        self._trace_mod.traced_run = self._orig_traced_run
        self._tmp.cleanup()

    def test_sealed_hook_members_pass_the_gate(self):
        """An archive carrying its sealed hook must seal successfully."""
        stub = _mk_archive_stub(self.tmp)
        pkg = _mk_pkg()
        pkg.template_path = self.recipe / "package.yml"
        new_files = [str(self.payload / "mybin"),
                     str(self.payload / "myconf")]
        ok = stub.pkg_archive_from_files(pkg, new_files)
        self.assertTrue(
            ok,
            f"a correct archive with a sealed hook failed the seal — the "
            f"member-count gate is not counting ./.scripts/ members; "
            f"errors: {stub.logger.errors}")
        archive = stub.pkg_archives / "demo-1.0.igos.tar.gz"
        with tarfile.open(archive, "r:gz") as t:
            members = sorted(t.getnames())
        self.assertIn("./.scripts/post_install.sh", members,
                      "the sealed hook must actually be in the archive")
        # Exactly: payload + ./.PKGINFO + the one sealed hook.
        self.assertEqual(len(members), len(new_files) + 2, members)

    def test_gate_still_refuses_true_contamination_with_hooks_sealed(self):
        """Counting sealed hooks must not blunt the gate's real purpose."""
        self._trace_mod.traced_run = lambda cmd, **kw: subprocess.run(
            [a for a in cmd if a != "--no-recursion"],
            capture_output=True, text=True)
        stub = _mk_archive_stub(self.tmp)
        pkg = _mk_pkg()
        pkg.template_path = self.recipe / "package.yml"
        contaminated = self.payload / "shared.d"
        contaminated.mkdir()
        (contaminated / "other.conf").write_text("not mine")
        new_files = [str(contaminated), str(self.payload / "mybin")]
        ok = stub.pkg_archive_from_files(pkg, new_files)
        self.assertFalse(
            ok, "recursion contamination must still fail with hooks sealed")
        self.assertTrue(any("member-count gate FAILED" in e
                            for e in stub.logger.errors), stub.logger.errors)


if __name__ == "__main__":
    unittest.main()
