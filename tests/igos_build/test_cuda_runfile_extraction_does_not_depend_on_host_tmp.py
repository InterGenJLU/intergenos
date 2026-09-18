#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The CUDA toolkit extraction must not depend on the build host's /tmp.

WHAT WENT WRONG, measured 2026-09-17 on a machine with 15 GiB of memory and a
7.7 GiB tmpfs /tmp. The recipe called NVIDIA's runfile as

    sh <runfile> --nox11 --extract=<work area>

and the wrapper unpacks ITSELF into a scratch directory before it writes
anything to --extract. Without --tmpdir that scratch directory is /tmp. The
build therefore carried an undeclared requirement — that the build host's /tmp
hold the whole uncompressed payload — which no recipe, rule or document stated
and which is untrue wherever /tmp is a tmpfs. The wrapper printed

    Extraction failed.
    Ensure there is enough space in /tmp and that the installation package is
    not corrupt

and exited 15 after 37 seconds, and the build failed in its configure phase.

The recipe now passes --tmpdir pointing inside the same work area the builder
already needs room in, checks the extraction's exit status itself with an error
that names the space requirement, and removes the scratch copy afterwards.

These tests run the recipe's own extraction block with `sh` stubbed, so the
behaviour is exercised without the 4 GB runfile.
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUILD_SH = REPO / "packages" / "compute" / "llama-cpp-cuda" / "build.sh"

# The extraction block, lifted from configure() rather than paraphrased: from
# the line that clears the directories to the line that removes the scratch
# copy. If the block is restructured these tests must be restructured with it.
_BLOCK_RE = re.compile(
    r'^\s*rm -rf "\$CUDA_ROOT".*?^\s*rm -rf "\$SRC_PARENT/cuda-tmp"\s*$',
    re.S | re.M)


def _extraction_block():
    m = _BLOCK_RE.search(BUILD_SH.read_text())
    assert m, ("the extraction block is not where these tests expect it in "
               f"{BUILD_SH}")
    return m.group(0)


class TheInvocation(unittest.TestCase):
    """What the recipe asks NVIDIA's wrapper to do."""

    def test_the_wrapper_is_given_its_own_scratch_directory(self):
        self.assertIn("--tmpdir=", _extraction_block(),
                      "without --tmpdir the wrapper unpacks itself into the "
                      "host's /tmp and the build inherits its size")

    def test_the_scratch_directory_is_in_the_work_area_not_tmp(self):
        block = _extraction_block()
        m = re.search(r'--tmpdir="([^"]+)"', block)
        self.assertIsNotNone(m, "--tmpdir must be given a quoted path")
        path = m.group(1)
        self.assertTrue(path.startswith("$SRC_PARENT/"),
                        f"--tmpdir is {path!r}; it must be derived from the "
                        "work area the builder already sized")
        self.assertNotIn("/tmp/", path)

    def test_the_extract_only_flags_are_still_there(self):
        # --extract keeps NVIDIA's installer from ever running (decision 5) and
        # --nox11 keeps it from looking for a terminal. Neither may be lost to
        # an edit that was only meant to move the scratch directory.
        block = _extraction_block()
        self.assertIn("--extract=", block)
        self.assertIn("--nox11", block)

    def test_the_recipe_records_the_measurement_behind_the_flag(self):
        # The next person to read this block needs the measurement, not the
        # conclusion: which filesystem, how much was free, what the wrapper
        # said. Pinned on the figures rather than on a sentence, because a
        # comment can be reworded and the numbers cannot.
        text = BUILD_SH.read_text()
        for figure in ("7.7 GiB", "8,041,478,069", "exited 15"):
            with self.subTest(figure=figure):
                self.assertIn(figure, text)


class TheExtractionBlockRunning(unittest.TestCase):
    """The block itself, with `sh` stubbed to stand in for the runfile."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.work = self.root / "work"
        (self.work / "src").mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.record = self.root / "argv.txt"

    def _stub_sh(self, exit_code):
        stub = self.bin / "sh"
        stub.write_text(
            "#!/bin/bash\n"
            f'printf "%s\\n" "$@" > "{self.record}"\n'
            # Stand in for the wrapper's own scratch writes, so a test can see
            # whether the block cleans them up.
            'for a in "$@"; do\n'
            '  case "$a" in --tmpdir=*) mkdir -p "${a#--tmpdir=}" && '
            'echo scratch > "${a#--tmpdir=}/payload" ;; esac\n'
            'done\n'
            f"exit {exit_code}\n")
        stub.chmod(0o755)

    def _run(self, exit_code):
        self._stub_sh(exit_code)
        script = (
            "set -u\n"
            f'PATH="{self.bin}:$PATH"\n'
            f'SRC_PARENT="{self.work}"\n'
            f'CUDA_ROOT="{self.work}/cuda-toolkit-root"\n'
            f'RUN_PATH="{self.root}/cuda.run"\n'
            'CUDA_RUN="cuda.run"\n'
            "extract() {\n"
            f"{_extraction_block()}\n"
            "}\n"
            "extract\n")
        path = self.root / "block.sh"
        path.write_text(script)
        return subprocess.run(["bash", str(path)],
                              capture_output=True, text=True)

    def test_a_failed_extraction_is_caught_by_the_recipe_itself(self):
        # Exit 15 is what the wrapper returned on the machine that ran out of
        # room. The recipe must not leave this to the caller's errexit.
        r = self._run(15)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("did not extract", r.stderr)

    def test_the_failure_message_points_at_the_space(self):
        r = self._run(15)
        self.assertIn("free space", r.stderr)
        self.assertIn("cuda-tmp", r.stderr)

    def test_the_wrapper_is_called_with_a_scratch_path_that_exists(self):
        self._run(0)
        argv = self.record.read_text().splitlines()
        tmpdir = [a[len("--tmpdir="):] for a in argv if a.startswith("--tmpdir=")]
        self.assertEqual(len(tmpdir), 1, argv)
        self.assertTrue(tmpdir[0].startswith(str(self.work)),
                        f"{tmpdir[0]} is outside the work area")
        self.assertFalse(tmpdir[0].startswith(os.path.realpath("/tmp") + "/")
                         and not tmpdir[0].startswith(str(self.work)))

    def test_the_scratch_copy_is_removed_after_a_successful_extraction(self):
        r = self._run(0)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.work / "cuda-tmp").exists(),
                         "the wrapper's scratch copy is as large as the payload "
                         "and nothing downstream reads it")

    def test_a_previous_runs_directories_are_cleared_first(self):
        stale = self.work / "cuda-extract" / "stale-component"
        stale.mkdir(parents=True)
        (self.work / "cuda-toolkit-root").mkdir(parents=True)
        (self.work / "cuda-toolkit-root" / "stale-file").write_text("x")
        self._run(0)
        self.assertFalse(stale.exists(),
                         "a previous extraction's payload must not survive into "
                         "this one")
        self.assertFalse((self.work / "cuda-toolkit-root" / "stale-file").exists())


if __name__ == "__main__":
    unittest.main()
