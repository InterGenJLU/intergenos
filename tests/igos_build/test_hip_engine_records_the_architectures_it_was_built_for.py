#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The HIP engine's architecture record must carry the architectures.

WHAT WENT WRONG, measured 2026-09-17 on an installed machine running ROCm
7.2.4. packages/compute/llama-cpp-hip/build.sh writes the list of GPU
architectures the engine was compiled for to
/opt/rocm/share/llama-cpp-hip/gpu-targets, and its do_install() wrote it from
$GPU_TARGETS — a variable configure() sets. Each recipe phase runs in its own
shell, so that variable is unset by the time do_install() runs, and the record
shipped EMPTY: one byte, a newline, whose
sha256 01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b is the
digest of that single byte.

Why an empty record is worse than no record. intergen.serving_device reads it
(hip_build_gpu_targets) and uses it to decide whether the installed HIP build
has device code for the GPU in front of it (hip_is_supported_here). That
function returns False — "do not use this engine here" — only when the machine's
architectures are known AND none of them is in the record. An empty record is
indistinguishable from an unreadable one, so it returns None, "I could not
tell", on every machine. The check therefore could not refuse anything, and the
engine it guards segfaults at model load on a card it has no code for. A gate
that cannot return its refusing answer is not a gate.

The builder exports the declaration into every phase as IGOS_GPU_TARGETS
(igos-build/builder.py), which the sibling CUDA recipe already consumes in its
own do_install(). This one now does the same, with the :? form so a missing
declaration fails the build rather than producing another silent record.
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HIP_BUILD_SH = REPO / "packages" / "compute" / "llama-cpp-hip" / "build.sh"
HIP_YML = REPO / "packages" / "compute" / "llama-cpp-hip" / "package.yml"

RECORD_LINE_RE = re.compile(r"^\s*printf '%s\\n' \"([^\"]+)\" > gpu-targets\.txt\s*$",
                            re.M)


class TheRecordIsWrittenFromTheExportedDeclaration(unittest.TestCase):

    def test_the_record_line_reads_the_builders_exported_variable(self):
        m = RECORD_LINE_RE.search(HIP_BUILD_SH.read_text())
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
        m = RECORD_LINE_RE.search(HIP_BUILD_SH.read_text())
        self.assertNotRegex(m.group(1), r"\$\{?GPU_TARGETS\b")

    def test_no_phase_after_configure_reads_a_configure_time_variable(self):
        # The class, not just this line: any phase function other than
        # configure() that expands $GPU_TARGETS is reading a variable from a
        # shell that has already exited.
        text = HIP_BUILD_SH.read_text()
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
                self.assertNotRegex(code, r"\$\{?GPU_TARGETS\b",
                                    f"{phase}() reads configure()'s variable")


class TheRecordWritingFragmentRunning(unittest.TestCase):
    """The two lines themselves, run with the phase environment reproduced."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        m = RECORD_LINE_RE.search(HIP_BUILD_SH.read_text())
        assert m, "record line not found"
        self.line = m.group(0).strip()

    def _run(self, env_line):
        script = (f"set -e\ncd {self.root}\n{env_line}\n{self.line}\n")
        return subprocess.run(["bash", "-c", script],
                              capture_output=True, text=True)

    def test_with_the_declaration_exported_the_record_carries_it(self):
        # The phase environment as the builder makes it: IGOS_GPU_TARGETS set,
        # GPU_TARGETS absent — which is exactly the case that shipped empty.
        r = self._run('export IGOS_GPU_TARGETS="gfx1100;gfx1102;gfx1201"\n'
                      'unset GPU_TARGETS')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.root / "gpu-targets.txt").read_text(),
                         "gfx1100;gfx1102;gfx1201\n")

    def test_without_a_declaration_the_build_fails_rather_than_writing_nothing(self):
        r = self._run("unset IGOS_GPU_TARGETS\nunset GPU_TARGETS")
        self.assertNotEqual(r.returncode, 0,
                            "an undeclared target list must stop the build")
        written = self.root / "gpu-targets.txt"
        self.assertFalse(written.exists() and written.read_text().strip(),
                         "no record is better than an empty one")

    def test_the_shipped_empty_record_is_what_this_prevents(self):
        # The exact artifact measured on the installed machine: one newline.
        # Reproduced here from the OLD expansion so the failure this test
        # prevents is visible rather than described.
        script = (f"set -e\ncd {self.root}\nunset GPU_TARGETS\n"
                  "printf '%s\\n' \"${GPU_TARGETS:-}\" > shipped.txt\n")
        subprocess.run(["bash", "-c", script], check=True)
        self.assertEqual((self.root / "shipped.txt").read_bytes(), b"\n")


class TheRecordMatchesTheDeclaration(unittest.TestCase):

    def test_the_recipe_declares_a_non_empty_target_list(self):
        declared = None
        for line in HIP_YML.read_text().splitlines():
            if line.startswith("gpu_targets:"):
                declared = line.split(":", 1)[1].strip().strip('"')
                break
        self.assertTrue(declared, "the recipe declares no gpu_targets")
        for token in declared.split(";"):
            with self.subTest(token=token):
                self.assertRegex(token.strip(), r"^gfx[0-9a-z]+$")


class TheConsumerCannotRefuseOnAnEmptyRecord(unittest.TestCase):
    """Why the empty record mattered, pinned against the real reader."""

    def setUp(self):
        import sys
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        from intergen import serving_device
        self.sd = serving_device
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)

    # The amdgpu driver publishes an ENCODED version, not a gfx name:
    # "gfx_target_version <major*10000 + minor*100 + step>", which
    # serving_device._gfx_name decodes. Checked against that function rather
    # than invented: 90012 -> gfx90c, 110000 -> gfx1100.
    GFX_TARGET_VERSION = {"gfx90c": 90012, "gfx1036": 100306,
                          "gfx1100": 110000, "gfx1102": 110002,
                          "gfx1201": 120001}

    def _topology_with(self, gfx_name):
        # A fresh root per call: two calls in one test must not leave the
        # parser reading both cards.
        root = self.root / f"topology-{gfx_name}"
        node = root / "0"
        node.mkdir(parents=True)
        encoded = self.GFX_TARGET_VERSION[gfx_name]
        # Written with the neighbouring keys the real file carries, so the
        # parser has to pick the right line rather than the only one.
        (node / "properties").write_text(
            "cpu_cores_count 0\n"
            "simd_count 16\n"
            f"gfx_target_version {encoded}\n"
            "max_waves_per_simd 32\n")
        return str(root)

    def test_the_fixture_decodes_to_the_architecture_it_names(self):
        # The fixture is only worth something if the real parser reads it as
        # the card it claims to be.
        for name in self.GFX_TARGET_VERSION:
            with self.subTest(gfx=name):
                self.assertEqual(
                    self.sd.detect_amd_gfx_targets(self._topology_with(name)),
                    {name})

    def test_an_empty_record_makes_the_refusal_unreachable(self):
        empty = self.root / "empty-record"
        empty.write_text("\n")
        self.assertEqual(self.sd.hip_build_gpu_targets(str(empty)), set())
        topology = self._topology_with("gfx90c")
        # gfx90c is in no build's target list, so the honest answer is False.
        # With an empty record the reader cannot give it.
        self.assertIsNone(
            self.sd.hip_is_supported_here(topology, str(empty)),
            "an empty record must not be able to look like a considered answer")

    def test_a_populated_record_can_refuse(self):
        record = self.root / "record"
        record.write_text("gfx1100;gfx1102;gfx1201\n")
        self.assertEqual(
            self.sd.hip_build_gpu_targets(str(record)),
            {"gfx1100", "gfx1102", "gfx1201"})
        self.assertIs(
            self.sd.hip_is_supported_here(self._topology_with("gfx90c"),
                                          str(record)),
            False,
            "with a real record the reader refuses a card the build has no "
            "code for — this is the answer the shipped record could not give")


if __name__ == "__main__":
    unittest.main()
