# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the L27 durable class fix: seed-state FHS-skeleton pruning.

Both builders pre-seed every DESTDIR with the merged-usr compat symlinks
(bin/lib/sbin/lib64 -> usr/*) + usr/{bin,lib,sbin} so installs follow the
live filesystem's layout — and then captured the seed into every archive
and manifest (GE-01: 908/913 archives carried ./bin ./lib ./sbin ./lib64;
evicting ONE mirror-only package deleted the chroot's compat symlinks).

Covers igos-build/builder.py prune_seeded_skeleton AND its bash twin
scripts/pkg-functions.sh pkg_prune_seeded_skeleton — the parity class runs
BOTH implementations against identical fixtures and requires identical
resulting trees, so the two builders cannot drift apart silently.
"""
import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

_builder = importlib.import_module("igos-build.builder")
prune_seeded_skeleton = _builder.prune_seeded_skeleton

PKG_FUNCTIONS = REPO_ROOT / "scripts" / "pkg-functions.sh"


def _seed(dest: Path, lib64_as_symlink: bool = False):
    """Recreate exactly what the builders pre-seed into a fresh DESTDIR."""
    for link in ("bin", "lib", "sbin"):
        os.symlink(f"usr/{link}", dest / link)
    if lib64_as_symlink:
        os.symlink("usr/lib64", dest / "lib64")
    else:
        (dest / "lib64").mkdir()
    for d in ("usr/bin", "usr/lib", "usr/sbin"):
        (dest / d).mkdir(parents=True)


def _tree(dest: Path) -> set[str]:
    """Relative member set of a staging tree (what tar/manifest would see)."""
    out = set()
    for p in dest.rglob("*"):
        rel = str(p.relative_to(dest))
        out.add(rel + "/" if p.is_dir() and not p.is_symlink() else rel)
    return out


class TestPruneSeededSkeleton(unittest.TestCase):
    def test_seed_only_members_pruned_real_content_kept(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            _seed(dest)
            (dest / "usr/bin/frob").write_text("#!/bin/sh\n")
            pruned = prune_seeded_skeleton("frob", dest)
            t = _tree(dest)
            self.assertNotIn("bin", t)
            self.assertNotIn("lib", t)
            self.assertNotIn("sbin", t)
            self.assertNotIn("lib64/", t)
            self.assertIn("usr/bin/frob", t)     # real content untouched
            self.assertIn("usr/", t)             # parent of real content kept
            self.assertNotIn("usr/lib/", t)      # empty seeded dirs collapse
            self.assertNotIn("usr/sbin/", t)
            self.assertTrue(pruned)

    def test_etc_only_package_collapses_whole_usr_seed(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            _seed(dest)
            (dest / "etc").mkdir()
            (dest / "etc/frob.conf").write_text("k=v\n")
            prune_seeded_skeleton("frob", dest)
            self.assertEqual(_tree(dest), {"etc/", "etc/frob.conf"})

    def test_populated_lib64_is_never_touched(self):
        """The glibc shape: /lib64 carries the loader — rmdir must refuse."""
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            _seed(dest)
            os.symlink("../usr/lib/ld-linux-x86-64.so.2",
                       dest / "lib64/ld-linux-x86-64.so.2")
            prune_seeded_skeleton("glibc", dest)
            self.assertIn("lib64/", _tree(dest))
            self.assertIn("lib64/ld-linux-x86-64.so.2", _tree(dest))

    def test_canonical_owner_is_exempt(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            _seed(dest)
            before = _tree(dest)
            pruned = prune_seeded_skeleton("intergenos-base-files", dest)
            self.assertEqual(pruned, [])
            self.assertEqual(_tree(dest), before)

    def test_non_seed_symlink_is_kept(self):
        """A symlink NOT in seed state (different target) is a package's own
        deliberate member — never removed on a name match alone."""
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            os.symlink("/opt/frob/bin", dest / "bin")
            prune_seeded_skeleton("frob", dest)
            self.assertIn("bin", _tree(dest))

    def test_lib64_symlink_seed_shape_pruned(self):
        """The Python builder mirrors a host whose /lib64 is a symlink —
        that seed shape must prune identically."""
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            _seed(dest, lib64_as_symlink=True)
            (dest / "usr/bin/frob").write_text("x\n")
            prune_seeded_skeleton("frob", dest)
            self.assertNotIn("lib64", _tree(dest))


class TestBashParity(unittest.TestCase):
    """Run the bash twin on identical fixtures; the resulting trees must be
    byte-identical to the Python implementation's — drift between the two
    builders is the failure this class exists to catch."""

    def _run_bash(self, name: str, dest: Path):
        # pkg_log writes through the sourced file's logging plumbing, which
        # is chroot-oriented — override it post-source for the unit run.
        script = (
            f"set -e; source {PKG_FUNCTIONS}; pkg_log() {{ :; }}; "
            f"pkg_prune_seeded_skeleton {name} {dest}"
        )
        r = subprocess.run(["bash", "-c", script],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _parity(self, name: str, populate):
        with tempfile.TemporaryDirectory() as tp, \
             tempfile.TemporaryDirectory() as tb:
            py_dest, sh_dest = Path(tp), Path(tb)
            populate(py_dest)
            populate(sh_dest)
            prune_seeded_skeleton(name, py_dest)
            self._run_bash(name, sh_dest)
            self.assertEqual(_tree(py_dest), _tree(sh_dest),
                             f"python vs bash prune diverged for {name}")

    def test_parity_seed_plus_content(self):
        def populate(d):
            _seed(d)
            (d / "usr/bin/frob").write_text("x\n")
        self._parity("frob", populate)

    def test_parity_etc_only(self):
        def populate(d):
            _seed(d)
            (d / "etc").mkdir()
            (d / "etc/a.conf").write_text("k\n")
        self._parity("frob", populate)

    def test_parity_populated_lib64(self):
        def populate(d):
            _seed(d)
            (d / "lib64/ld.so").write_text("ELF\n")
        self._parity("glibc", populate)

    def test_parity_owner_exempt(self):
        self._parity("intergenos-base-files", _seed)


if __name__ == "__main__":
    unittest.main()
