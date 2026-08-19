# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Chapter-8 build must clear the temporary toolchain's unowned residue.

THE DEFECT THIS PINS. The LFS chapter 5-7 temporary toolchain installs files
onto the live chroot root. A final-system package's DESTDIR deploy overlays
its own files on top but never deletes anything, so every path the final
recipe deliberately drops keeps the temporary toolchain's copy for the life of
the chroot. packages/core/python/build.sh removes idlelib and /usr/bin/idle3*
from its DESTDIR (InterGenOS builds Python without tkinter, so IDLE cannot
run), and the chapter-7 Python's copies stayed on the root. The same shape
left the chapter-6 libstdc++ GDB pretty-printer loader at
/usr/lib/libstdc++.so.6.0.34-gdb.py while the final GCC ships its printers
under /usr/share/gdb/auto-load/.

That residue is unowned content in the shipping tree. build-squashfs Step 4.85
fails closed on unowned files, and on the 2026-08-15 from-scratch build — the
first from-scratch run since that gate landed — it stopped the pipeline with
166 findings, 162 of which were this class. They were removed by hand. A
from-scratch build rebuilds the residue every time, so the disposition has to
be a build step, not a hand pass at the most expensive discovery point.

WHAT THE SWEEP MUST GUARANTEE, and what each test below measures:

  * a declared residue pattern's unowned matches are removed;
  * a match that an installed package's manifest RECORDS is never removed and
    is reported — an owned match means the pattern is wrong or a recipe
    changed, and a silent skip would hide that;
  * with no manifests to read, the sweep refuses to delete anything: an
    ownership check that cannot see ownership is not a check;
  * every declared pattern is accounted for in the output, including the ones
    that matched nothing, so a pattern that has gone stale cannot pass as
    "clean";
  * the Chapter-8 driver runs it and stops the build when it fails.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
SWEEP = REPO_ROOT / "scripts" / "ch8-residue-sweep.py"
PATTERNS = REPO_ROOT / "config" / "ch8-residue-patterns.txt"
CH8 = REPO_ROOT / "scripts" / "chroot-build-ch8.sh"

MANIFEST_TEMPLATE = """PACKAGE NAME: {name}-{version}
PACKAGE VERSION: {version}
UNCOMPRESSED SIZE: 1K (1024 bytes)
BUILD DATE: 2026-08-19T00:00:00Z
BUILD SYSTEM: InterGenOS LFS 13.0
DESCRIPTION:
{name}: test fixture

FILE LIST:
{files}
"""


def write_manifest(pkg_db: Path, name: str, version: str, entries):
    """Write a manifest in the exact shape scripts/pkg-functions.sh emits:
    directories carry a trailing slash, files carry a ` sha256:<64 hex>`
    suffix anchored at end of line."""
    lines = []
    for entry in entries:
        if entry.endswith("/"):
            lines.append(entry)
        else:
            lines.append(f"{entry} sha256:{'0' * 64}")
    (pkg_db / f"{name}-{version}").write_text(
        MANIFEST_TEMPLATE.format(name=name, version=version,
                                 files="\n".join(lines)))


def make_tree(root: Path, paths):
    """Create each relative path under root. A trailing slash means directory."""
    for p in paths:
        target = root / p.rstrip("/")
        if p.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x")


def run_sweep(root: Path, pkg_db: Path, patterns: Path, *extra):
    return subprocess.run(
        [sys.executable, str(SWEEP),
         "--root", str(root),
         "--package-db", str(pkg_db),
         "--patterns", str(patterns), *extra],
        capture_output=True, text=True)


class SweepBehaviour(unittest.TestCase):

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "root"
        self.pkg_db = self.base / "pkgdb"
        self.root.mkdir()
        self.pkg_db.mkdir()
        self.patterns = self.base / "patterns.txt"
        self.patterns.write_text(
            "usr/lib/python3.*/idlelib/**  temp-python IDLE library\n"
            "usr/bin/idle3*                temp-python IDLE launchers\n"
            "usr/lib/libstdc++.so.*-gdb.py  temp-libstdc++ printer loader\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_unowned_residue_is_removed(self):
        make_tree(self.root, [
            "usr/lib/python3.14/idlelib/idle.py",
            "usr/lib/python3.14/idlelib/__pycache__/idle.cpython-314.pyc",
            "usr/bin/idle3",
            "usr/bin/idle3.14",
            "usr/lib/libstdc++.so.6.0.34-gdb.py",
            # Neighbours the sweep must leave alone.
            "usr/bin/python3",
            "usr/lib/python3.14/os.py",
        ])
        write_manifest(self.pkg_db, "python", "3.14.3", [
            "usr/", "usr/bin/", "usr/bin/python3",
            "usr/lib/", "usr/lib/python3.14/",
        ])
        r = run_sweep(self.root, self.pkg_db, self.patterns)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.root / "usr/lib/python3.14/idlelib").exists())
        self.assertFalse((self.root / "usr/bin/idle3").exists())
        self.assertFalse((self.root / "usr/bin/idle3.14").exists())
        self.assertFalse(
            (self.root / "usr/lib/libstdc++.so.6.0.34-gdb.py").exists())
        # The directory the final python DOES claim must survive.
        self.assertTrue((self.root / "usr/lib/python3.14").is_dir())
        self.assertTrue((self.root / "usr/bin/python3").exists())
        # An unowned neighbour that no pattern names is NOT the sweep's
        # business: the patterns select, they do not license a general purge.
        self.assertTrue((self.root / "usr/lib/python3.14/os.py").exists())

    def test_owned_match_is_kept_and_reported(self):
        """A pattern match that a package records is a pattern defect, not a
        stray. It must survive, and it must be named."""
        make_tree(self.root, ["usr/bin/idle3", "usr/bin/idle3.14"])
        write_manifest(self.pkg_db, "python", "3.14.3", [
            "usr/", "usr/bin/", "usr/bin/idle3",
        ])
        r = run_sweep(self.root, self.pkg_db, self.patterns)
        self.assertTrue((self.root / "usr/bin/idle3").exists(),
                        "a recorded path was deleted: " + r.stdout + r.stderr)
        self.assertFalse((self.root / "usr/bin/idle3.14").exists())
        self.assertIn("usr/bin/idle3", r.stdout)
        self.assertIn("recorded by an installed package", r.stdout)

    def test_refuses_when_no_manifests_readable(self):
        """No manifests means ownership is unknown. Deleting on an unknown
        ownership set is exactly the silent-failure shape the sweep exists to
        remove, so it must refuse instead."""
        make_tree(self.root, ["usr/bin/idle3"])
        r = run_sweep(self.root, self.pkg_db, self.patterns)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((self.root / "usr/bin/idle3").exists())
        self.assertIn("no package manifests", (r.stdout + r.stderr).lower())

    def test_missing_package_db_refuses(self):
        make_tree(self.root, ["usr/bin/idle3"])
        r = run_sweep(self.root, self.base / "absent", self.patterns)
        self.assertEqual(r.returncode, 2)
        self.assertTrue((self.root / "usr/bin/idle3").exists())

    def test_dry_run_removes_nothing(self):
        make_tree(self.root, ["usr/bin/idle3"])
        write_manifest(self.pkg_db, "python", "3.14.3", ["usr/", "usr/bin/"])
        r = run_sweep(self.root, self.pkg_db, self.patterns, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.root / "usr/bin/idle3").exists())
        self.assertIn("usr/bin/idle3", r.stdout)

    def test_every_pattern_is_accounted_for_including_zero_matches(self):
        """A pattern that matches nothing is reported by name. A summary that
        prints only what it found cannot distinguish a clean root from a
        pattern that has silently gone stale."""
        make_tree(self.root, ["usr/bin/idle3"])
        write_manifest(self.pkg_db, "python", "3.14.3", ["usr/", "usr/bin/"])
        r = run_sweep(self.root, self.pkg_db, self.patterns)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for pattern in ("usr/lib/python3.*/idlelib/**",
                        "usr/bin/idle3*",
                        "usr/lib/libstdc++.so.*-gdb.py"):
            self.assertIn(pattern, r.stdout)
        self.assertIn("matched nothing", r.stdout)

    def test_removal_record_names_every_removed_path(self):
        make_tree(self.root, ["usr/bin/idle3", "usr/bin/idle3.14"])
        write_manifest(self.pkg_db, "python", "3.14.3", ["usr/", "usr/bin/"])
        record = self.base / "removed.txt"
        r = run_sweep(self.root, self.pkg_db, self.patterns,
                      "--record", str(record))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        written = record.read_text()
        self.assertIn("usr/bin/idle3\n", written)
        self.assertIn("usr/bin/idle3.14\n", written)

    def test_symlink_residue_is_removed_without_following_it(self):
        """A dangling or in-tree symlink is residue like any other path, and
        removing it must never touch what it points at."""
        (self.root / "usr/bin").mkdir(parents=True)
        (self.root / "usr/bin/real").write_text("keep me")
        os.symlink("real", self.root / "usr/bin/idle3")
        write_manifest(self.pkg_db, "python", "3.14.3",
                       ["usr/", "usr/bin/", "usr/bin/real"])
        r = run_sweep(self.root, self.pkg_db, self.patterns)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.lexists(self.root / "usr/bin/idle3"))
        self.assertEqual((self.root / "usr/bin/real").read_text(), "keep me")


    def test_pseudo_filesystems_and_build_tree_binds_are_not_walked(self):
        """Inside the chroot /proc, /sys, /dev, /run, /tmp, /sources and the
        build-tree binds are mount points, not chroot content. The squashfs
        ownership gate prunes exactly this set; the sweep must agree with it,
        or the two disagree about what chroot content even is."""
        patterns = self.base / "wide.txt"
        # A glob that matches an idle3 file at ANY depth, so nothing but the
        # prune itself can save a copy planted under a skipped tree.
        patterns.write_text("*idle3  every idle3 at any depth\n")
        for skipped in ("proc", "sys", "dev", "run", "tmp", "sources"):
            make_tree(self.root, [f"{skipped}/idle3"])
        make_tree(self.root, ["mnt/intergenos/idle3", "mnt/hot-storage/idle3"])
        make_tree(self.root, ["usr/bin/idle3"])
        write_manifest(self.pkg_db, "python", "3.14.3", ["usr/", "usr/bin/"])
        r = run_sweep(self.root, self.pkg_db, patterns)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for skipped in ("proc", "sys", "dev", "run", "tmp", "sources"):
            self.assertTrue((self.root / skipped / "idle3").exists(),
                            f"/{skipped} was walked")
        self.assertTrue((self.root / "mnt/intergenos/idle3").exists())
        self.assertTrue((self.root / "mnt/hot-storage/idle3").exists())
        self.assertFalse((self.root / "usr/bin/idle3").exists(),
                         "the sweep did not run at all")


    def test_unreadable_directory_fails_instead_of_reporting_clean(self):
        """os.walk swallows directory-read errors by default. A tree the
        sweep could not read is a tree it cannot certify; reporting PASS over
        a partially-read root is the exact silent-failure shape this step
        exists to remove."""
        if os.geteuid() == 0:
            self.skipTest("running as root: mode bits do not deny the walk")
        make_tree(self.root, ["usr/bin/idle3", "usr/share/locked/file"])
        write_manifest(self.pkg_db, "python", "3.14.3", ["usr/", "usr/bin/"])
        locked = self.root / "usr/share/locked"
        os.chmod(locked, 0o000)
        try:
            r = run_sweep(self.root, self.pkg_db, self.patterns)
        finally:
            os.chmod(locked, 0o755)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("could not be read", r.stdout)
        self.assertNotIn("PASS", r.stdout)


class ShippedPatternsFile(unittest.TestCase):

    def test_patterns_file_exists_and_every_pattern_carries_a_reason(self):
        self.assertTrue(PATTERNS.is_file(), f"{PATTERNS} missing")
        entries = 0
        for i, raw in enumerate(PATTERNS.read_text().splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            entries += 1
            parts = __import__("re").split(r"\t+| {2,}", line, maxsplit=1)
            self.assertEqual(len(parts), 2,
                             f"line {i} has no reason column: {raw!r}")
            self.assertTrue(parts[1].strip(),
                            f"line {i} has an empty reason: {raw!r}")
        self.assertGreater(entries, 0, "patterns file declares nothing")

    def test_shipped_patterns_cover_the_measured_residue_classes(self):
        text = PATTERNS.read_text()
        self.assertIn("idlelib", text)
        self.assertIn("idle3", text)
        self.assertIn("gdb.py", text)


class Ch8DriverWiring(unittest.TestCase):

    def test_ch8_invokes_the_sweep_with_absolute_paths_and_fails_the_build(self):
        text = CH8.read_text()
        self.assertIn("ch8-residue-sweep.py", text,
                      "Chapter 8 does not run the residue sweep")
        self.assertIn("/mnt/intergenos/scripts/ch8-residue-sweep.py", text,
                      "the sweep must be invoked by absolute path (Rule H)")
        self.assertIn("/mnt/intergenos/config/ch8-residue-patterns.txt", text,
                      "the patterns file must be passed by absolute path")
        # The invocation must be guarded so a non-zero exit stops the build,
        # in the same shape 8.87's backfill uses.
        idx = text.index("ch8-residue-sweep.py")
        window = text[max(0, idx - 800):idx + 800]
        self.assertIn("exit 1", window,
                      "a failing residue sweep must stop the Chapter-8 build")


if __name__ == "__main__":
    unittest.main()
