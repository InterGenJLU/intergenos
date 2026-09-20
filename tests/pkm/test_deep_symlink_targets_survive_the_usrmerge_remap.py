# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A symlink below the root keeps the target its package shipped.

The installer remaps archive members for a merged-/usr layout: when the target
root's /lib is a symlink to usr/lib, a member named lib/foo is written to
usr/lib/foo instead. Linknames are remapped too, and that is necessary for
HARDLINKS at any depth, because a hardlink's linkname names a path from the
archive root and tar has to find the member it refers to under the name the
filter actually wrote.

A SYMLINK's relative target is not a path from the archive root. It is resolved
against the directory the symlink itself sits in. Remapping it is therefore
correct only for a symlink that sits AT the root, where those two happen to be
the same directory. Applied deeper it rewrites a target that was already right.

Measured on an installed machine on 2026-09-20: the GPU compiler package ships
opt/rocm/llvm as a symlink to lib/llvm, meaning /opt/rocm/lib/llvm, its own
sibling. The filter rewrote it to usr/lib/llvm, so the installed link pointed at
/opt/rocm/usr/lib/llvm, which does not exist. Nothing reported it: the link was
created, the install verified, and the broken target only surfaced when a person
followed the path. That is the failure this file pins.
"""

import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from pkm.installer import _safe_extract_tar


def _sym(tf, name, target):
    ti = tarfile.TarInfo(name)
    ti.type = tarfile.SYMTYPE
    ti.mode = 0o777
    ti.linkname = target
    tf.addfile(ti)


def _reg(tf, name, data=b"x\n"):
    import io

    ti = tarfile.TarInfo(name)
    ti.type = tarfile.REGTYPE
    ti.mode = 0o644
    ti.size = len(data)
    tf.addfile(ti, io.BytesIO(data))


def _hard(tf, name, target):
    ti = tarfile.TarInfo(name)
    ti.type = tarfile.LNKTYPE
    ti.mode = 0o644
    ti.linkname = target
    tf.addfile(ti)


class TestDeepSymlinkTargets(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dest = self.tmp / "root"
        # A merged-/usr target root: /lib and /bin are symlinks into /usr.
        (self.dest / "usr/lib").mkdir(parents=True)
        (self.dest / "usr/bin").mkdir(parents=True)
        os.symlink("usr/lib", self.dest / "lib")
        os.symlink("usr/bin", self.dest / "bin")
        self.archive = self.tmp / "pkg.igos.tar.gz"

    def _extract(self, build):
        with tarfile.open(self.archive, "w:gz") as tf:
            build(tf)
        ok, message = _safe_extract_tar(str(self.archive), str(self.dest))
        self.assertTrue(ok, f"extraction failed: {message}")

    def test_a_symlink_below_the_root_keeps_its_own_relative_target(self):
        """The shape that broke the GPU compiler's layout."""

        def build(tf):
            _reg(tf, "./opt/rocm/lib/llvm/bin/clang")
            _sym(tf, "./opt/rocm/llvm", "lib/llvm")

        self._extract(build)
        link = self.dest / "opt/rocm/llvm"
        self.assertTrue(link.is_symlink(), "the shipped symlink was not created")
        self.assertEqual(
            os.readlink(link),
            "lib/llvm",
            "the installer rewrote a symlink target that is resolved against the "
            "link's own directory, not against the root; the installed link now "
            "points at a path the package never shipped",
        )
        self.assertTrue(
            link.resolve().is_dir(),
            f"the installed symlink does not resolve: {link} -> {os.readlink(link)}",
        )

    def test_a_symlink_at_the_root_still_has_its_target_remapped(self):
        """At the root the target IS root-relative, so the remap applies."""

        def build(tf):
            _reg(tf, "./bin/init-program")
            _sym(tf, "./init", "bin/init-program")

        self._extract(build)
        self.assertEqual(
            os.readlink(self.dest / "init"),
            "usr/bin/init-program",
            "a root-level symlink's target is a path from the root and must "
            "follow the member it names into usr/",
        )

    def test_a_hardlink_below_the_root_still_has_its_target_remapped(self):
        """A hardlink's linkname names an archive member, at any depth."""

        def build(tf):
            _reg(tf, "./lib/gdk-pixbuf-2.0/loaders.so")
            _hard(tf, "./usr/lib/gdk-pixbuf-2.0/loaders-alias.so",
                  "lib/gdk-pixbuf-2.0/loaders.so")

        self._extract(build)
        alias = self.dest / "usr/lib/gdk-pixbuf-2.0/loaders-alias.so"
        self.assertTrue(
            alias.exists(),
            "the hardlink did not extract; its linkname must follow the real "
            "member into usr/ or tar cannot resolve it",
        )

    def test_a_deep_symlink_that_walks_out_of_its_directory_is_untouched(self):
        """../ targets were never remapped and must stay that way."""

        def build(tf):
            _reg(tf, "./opt/rocm/llvm/bin/amdclang")
            _sym(tf, "./opt/rocm/bin/amdclang", "../llvm/bin/amdclang")

        self._extract(build)
        self.assertEqual(
            os.readlink(self.dest / "opt/rocm/bin/amdclang"),
            "../llvm/bin/amdclang",
        )


if __name__ == "__main__":
    unittest.main()
