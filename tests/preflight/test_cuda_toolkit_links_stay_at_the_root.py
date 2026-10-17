#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Run the helper's real component merge on small extracted toolkit trees."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "packages/compute/cuda-toolkit/helper/igos-install-cuda-toolkit"
TARGET = Path("targets/x86_64-linux")
OLD_LINKS = {
    TARGET / "include/include": "targets/x86_64-linux/include",
    TARGET / "lib/lib64": "targets/x86_64-linux/lib",
}


def install_step(extracted, prefix):
    source = HELPER.read_text()
    start = source.index("# ---- Lay the toolkit into")
    end = source.index("# ---- Loader path", start)
    return subprocess.run(
        ["/bin/bash", "-eu", "-c", source[start:end]],
        env={**os.environ, "EXTRACT_DIR": str(extracted), "CUDA_PREFIX": str(prefix)},
        capture_output=True, text=True, timeout=30,
    )


def make_components(extracted):
    for name, library in (("cuda_cudart", "libcudart.so"), ("libcublas", "libcublas.so")):
        component = extracted / name
        (component / TARGET / "include").mkdir(parents=True)
        (component / TARGET / "lib").mkdir()
        (component / "include").symlink_to(TARGET / "include")
        (component / "lib64").symlink_to(TARGET / "lib")
        (component / TARGET / "lib" / library).write_text(name)
    (extracted / "cuda_cudart" / TARGET / "include/cuda_runtime.h").write_text("header")
    (extracted / "cuda_nvcc/bin").mkdir(parents=True)
    (extracted / "cuda_nvcc/bin/nvcc").write_text("compiler")
    (extracted / "EULA.txt").write_text("fixture agreement")
    (extracted / "version.json").write_text("{}")


class ToolkitLinksStayAtTheRoot(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.extracted = self.root / "extracted"
        self.prefix = self.root / "cuda"
        make_components(self.extracted)

    def install(self):
        result = install_step(self.extracted, self.prefix)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name, target in (("include", TARGET / "include"), ("lib64", TARGET / "lib")):
            self.assertTrue((self.prefix / name).is_symlink())
            self.assertEqual((self.prefix / name).readlink(), target)
            self.assertTrue((self.prefix / name).is_dir())

    def dangling(self):
        result = subprocess.run(
            ["/usr/bin/find", str(self.prefix), "-xtype", "l", "-print"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout

    def test_fresh_and_repeated_component_merges_have_only_root_links(self):
        self.install()
        self.install()
        self.assertEqual(self.dangling(), "")
        for name in OLD_LINKS:
            self.assertFalse((self.prefix / name).is_symlink())
        self.assertEqual((self.prefix / "lib64/libcudart.so").read_text(), "cuda_cudart")
        self.assertEqual((self.prefix / "lib64/libcublas.so").read_text(), "libcublas")

    def test_upgrade_removes_the_exact_old_dangling_pair(self):
        for name, target in OLD_LINKS.items():
            path = self.prefix / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
        self.assertEqual(len(self.dangling().splitlines()), 2)
        self.install()
        self.assertEqual(self.dangling(), "")
        for name in OLD_LINKS:
            self.assertFalse((self.prefix / name).is_symlink())

    def test_upgrade_preserves_different_links_and_regular_files_at_those_names(self):
        include = self.prefix / TARGET / "include/include"
        include.parent.mkdir(parents=True)
        include.symlink_to("different-target")
        library = self.prefix / TARGET / "lib/lib64"
        library.parent.mkdir(parents=True)
        library.write_text("keep this file")
        other = self.prefix / TARGET / "lib/other-link"
        other.symlink_to("another-missing-target")
        self.install()
        self.assertEqual(include.readlink(), Path("different-target"))
        self.assertEqual(library.read_text(), "keep this file")
        self.assertEqual(other.readlink(), Path("another-missing-target"))

    def test_upgrade_preserves_resolving_links_even_with_the_old_target_text(self):
        for name, target in OLD_LINKS.items():
            path = self.prefix / name
            (path.parent / target).mkdir(parents=True)
            path.symlink_to(target)
        self.install()
        self.assertEqual(self.dangling(), "")
        for name, target in OLD_LINKS.items():
            self.assertEqual((self.prefix / name).readlink(), Path(target))
            self.assertTrue((self.prefix / name).is_dir())


if __name__ == "__main__":
    unittest.main()
