#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The proprietary GPU driver's hooks name kernel paths by the release string
InterGenOS actually produces.

Found by an independent kernel-coupling review (2026-09-11): the post-install
hook's fallback kernel selector globbed /lib/modules/*-igos, which can match no
real directory (they are <version>-igos-<release>), and the signing hook's
fallback stripped only a trailing -igos from the release string, naming a
source directory that never exists (the second kernel pass stages
/usr/src/linux-<bare version>). Neither broke the normal upgrade path — the
kernel's own hook passes the release explicitly — both broke the documented
recovery paths (re-running the post-install hook by hand; signing when the
build tree's sign-file is missing).

The shared helper hooks/kernel-paths.sh now owns those derivations; these
tests exercise it against fake trees and hold the two hooks to using it.
"""
import os
import pathlib
import stat
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
HOOKS = REPO / "packages/extra/nvidia/hooks"
HELPER = HOOKS / "kernel-paths.sh"


def _bash(script, cwd):
    return subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True, text=True)


class HelperFunctionsTest(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="nvidia-kpaths-"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.assertTrue(HELPER.is_file(), f"{HELPER} missing")

    def _run(self, call):
        return _bash(f". '{HELPER}'; {call}", self.tmp)

    def test_kver_from_modules_selects_the_one_release_stamped_kernel(self):
        (self.tmp / "modules/6.18.51-igos-1/build").mkdir(parents=True)
        (self.tmp / "modules/6.18.10-igos-21").mkdir(parents=True)  # no build tree
        r = self._run(f"nvidia_kver_from_modules '{self.tmp}/modules'")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "6.18.51-igos-1")

    def test_kver_from_modules_prints_nothing_when_no_kernel_has_a_build_tree(self):
        (self.tmp / "modules/6.18.51-igos-1").mkdir(parents=True)
        r = self._run(f"nvidia_kver_from_modules '{self.tmp}/modules'")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_kver_from_modules_refuses_to_guess_between_two_kernels(self):
        (self.tmp / "modules/6.18.10-igos-21/build").mkdir(parents=True)
        (self.tmp / "modules/6.18.51-igos-1/build").mkdir(parents=True)
        r = self._run(f"nvidia_kver_from_modules '{self.tmp}/modules'")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("6.18.10-igos-21", r.stderr)
        self.assertIn("6.18.51-igos-1", r.stderr)

    def test_kernel_version_strips_the_whole_release_suffix(self):
        r = self._run("nvidia_kernel_version 6.18.51-igos-1; nvidia_kernel_version 6.18.10-igos-21")
        self.assertEqual(r.stdout.split(), ["6.18.51", "6.18.10"])

    def test_sign_file_path_prefers_the_build_tree_then_the_bare_version_source(self):
        m = self.tmp / "modules"; s = self.tmp / "src"
        primary = m / "6.18.51-igos-1/build/scripts/sign-file"
        fallback = s / "linux-6.18.51/scripts/sign-file"
        for p in (primary, fallback):
            p.parent.mkdir(parents=True)
            p.write_text("#!/bin/sh\n")
            p.chmod(0o755)
        r = self._run(f"nvidia_sign_file_path 6.18.51-igos-1 '{m}' '{s}'")
        self.assertEqual(r.stdout.strip(), str(primary))
        primary.chmod(0o644)  # the build tree's copy is not executable → the staged source
        r = self._run(f"nvidia_sign_file_path 6.18.51-igos-1 '{m}' '{s}'")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(fallback))

    def test_sign_file_path_fails_loudly_naming_both_tried_paths(self):
        m = self.tmp / "modules"; s = self.tmp / "src"
        m.mkdir(); s.mkdir()
        r = self._run(f"nvidia_sign_file_path 6.18.51-igos-1 '{m}' '{s}'")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn(f"{m}/6.18.51-igos-1/build/scripts/sign-file", r.stderr)
        self.assertIn(f"{s}/linux-6.18.51/scripts/sign-file", r.stderr)


class HooksUseTheHelperTest(unittest.TestCase):

    def test_post_install_uses_the_helper_and_carries_no_bare_igos_glob(self):
        text = (HOOKS / "post-install.sh").read_text()
        self.assertIn("kernel-paths.sh", text)
        self.assertIn("nvidia_kver_from_modules", text)
        self.assertNotIn("/lib/modules/*-igos;", text)
        self.assertNotIn("/lib/modules/*-igos ", text)
        self.assertNotIn('/lib/modules/*-igos"', text)

    def test_sign_module_uses_the_helper_and_carries_no_partial_suffix_strip(self):
        text = (HOOKS / "sign-module.sh").read_text()
        self.assertIn("kernel-paths.sh", text)
        self.assertIn("nvidia_sign_file_path", text)
        self.assertNotIn("${KVER%-igos}", text)

    def test_build_installs_the_helper_beside_the_hooks(self):
        text = (REPO / "packages/extra/nvidia/build.sh").read_text()
        self.assertIn("hooks/kernel-paths.sh", text)
        self.assertIn("/var/lib/pkm/hooks/nvidia/kernel-paths.sh", text)

    def test_helper_is_bash_and_defines_the_three_functions(self):
        text = HELPER.read_text()
        for fn in ("nvidia_kver_from_modules", "nvidia_kernel_version", "nvidia_sign_file_path"):
            self.assertIn(f"{fn}()", text)
        self.assertEqual(subprocess.run(["bash", "-n", str(HELPER)]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
