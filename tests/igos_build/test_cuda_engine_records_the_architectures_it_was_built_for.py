#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The CUDA engine's architecture record must be written from a variable that
exists when the record is written.

WHAT WENT WRONG, measured 2026-09-18 at the first real build of the record.
packages/compute/llama-cpp-cuda/build.sh writes the list of GPU architectures
the engine was compiled for to /opt/llama-cpp-cuda/share/llama-cpp-cuda/
gpu-targets, and its do_install() wrote it from $CUDA_ARCHS — a variable
configure() sets. Each recipe phase runs in its own shell, so that variable is
unset by the time do_install() runs. The line was guarded with the `:?` form,
so instead of shipping an empty record the build stopped in the install phase
with "CUDA_ARCHS is not set in do_install; refusing to write an empty
architecture record" — the guard did its job, and the recipe still could not
produce the archive.

The sibling HIP recipe had shipped the unguarded form of the same line and its
record went out EMPTY (one newline); it was fixed the same day to read the
builder's exported declaration. This recipe now reads the same declaration the
same way: IGOS_GPU_TARGETS is exported into every phase by igos-build/builder.py
from package.yml's gpu_targets, which is the value configure() read into
CUDA_ARCHS and handed to cmake — one source of truth, present where it is used.
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CUDA_BUILD_SH = REPO / "packages" / "compute" / "llama-cpp-cuda" / "build.sh"
CUDA_YML = REPO / "packages" / "compute" / "llama-cpp-cuda" / "package.yml"

RECORD_LINE_RE = re.compile(r"^\s*printf '%s\\n' \"([^\"]+)\" > gpu-targets\.txt\s*$",
                            re.M)


class TheRecordIsWrittenFromTheExportedDeclaration(unittest.TestCase):

    def test_the_record_line_reads_the_builders_exported_variable(self):
        m = RECORD_LINE_RE.search(CUDA_BUILD_SH.read_text())
        self.assertIsNotNone(m, "the gpu-targets record line is not where this "
                                "test expects it")
        expansion = m.group(1)
        self.assertIn("IGOS_GPU_TARGETS", expansion,
                      "do_install runs in its own shell; a variable configure() "
                      "set is not there")
        self.assertIn(":?", expansion,
                      "an absent declaration must fail the build, not write a "
                      "record that says nothing")

    def test_it_does_not_read_the_configure_time_variable(self):
        m = RECORD_LINE_RE.search(CUDA_BUILD_SH.read_text())
        self.assertNotRegex(m.group(1), r"\$\{?CUDA_ARCHS\b")

    def test_no_phase_after_configure_reads_a_configure_time_variable(self):
        # The class, not just this line: any phase function other than
        # configure() that expands $CUDA_ARCHS is reading a variable from a
        # shell that has already exited.
        text = CUDA_BUILD_SH.read_text()
        for phase in ("build", "check", "do_install"):
            start = text.find(f"\n{phase}() {{")
            if start < 0:
                continue
            end = text.find("\n}", start)
            body = text[start:end if end > 0 else len(text)]
            # Comments in this recipe NAME the variable while explaining why it
            # is not read; strip them so the test looks at code only.
            code = "\n".join(l for l in body.splitlines()
                             if not l.lstrip().startswith("#"))
            with self.subTest(phase=phase):
                self.assertNotRegex(code, r"\$\{?CUDA_ARCHS\b",
                                    f"{phase}() reads configure()'s variable")


class TheRecordWritingFragmentRunning(unittest.TestCase):
    """The line itself, run with the phase environment reproduced."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        m = RECORD_LINE_RE.search(CUDA_BUILD_SH.read_text())
        assert m, "record line not found"
        self.line = m.group(0).strip()

    def _run(self, env_line):
        script = (f"set -e\ncd {self.root}\n{env_line}\n{self.line}\n")
        return subprocess.run(["bash", "-c", script],
                              capture_output=True, text=True)

    def test_with_the_declaration_exported_the_record_carries_it(self):
        # The phase environment as the builder makes it: IGOS_GPU_TARGETS set,
        # CUDA_ARCHS absent — exactly the environment the first real build
        # stopped in.
        r = self._run('export IGOS_GPU_TARGETS="75-real;86-real;89-real"\n'
                      'unset CUDA_ARCHS')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.root / "gpu-targets.txt").read_text(),
                         "75-real;86-real;89-real\n")

    def test_without_a_declaration_the_build_fails_rather_than_writing_nothing(self):
        r = self._run("unset IGOS_GPU_TARGETS\nunset CUDA_ARCHS")
        self.assertNotEqual(r.returncode, 0,
                            "an undeclared target list must stop the build")
        written = self.root / "gpu-targets.txt"
        self.assertFalse(written.exists() and written.read_text().strip(),
                         "no record is better than an empty one")


class TheRecordMatchesTheDeclaration(unittest.TestCase):

    def test_the_recipe_declares_a_non_empty_target_list(self):
        declared = None
        for line in CUDA_YML.read_text().splitlines():
            if line.startswith("gpu_targets:"):
                declared = line.split(":", 1)[1].strip().strip('"')
                break
        self.assertTrue(declared, "the recipe declares no gpu_targets")
        for token in declared.split(";"):
            with self.subTest(token=token):
                self.assertRegex(token.strip(), r"^[0-9]+[a-z]?-real$")


if __name__ == "__main__":
    unittest.main()
