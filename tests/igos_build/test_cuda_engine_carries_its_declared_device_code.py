#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The CUDA engine must carry compiled kernels for every card it claims.

WHAT WENT WRONG, measured 2026-09-17 on an installed machine. The recipe
declared `75-virtual;80-virtual;86-real;89-real;120a-real;121a-real`: Turing and
the Ampere datacentre parts as PTX, the rest as compiled kernels. The reasoning
written beside it was that the driver JIT-compiles PTX, so a Turing card works
without carrying its kernels.

That reasoning holds only while the driver is at least as new as the toolkit
that emitted the PTX. On a GeForce GTX 1650 (compute capability 7.5) with driver
580.159.04 — which advertises CUDA 13.0 — against this recipe's pinned 13.3.1
toolkit, a five-line CUDA program built from that toolkit two ways answered:

    built as a real sm_75 ELF  -> the kernel ran, the value came back
    built as sm_75 PTX only    -> "the provided PTX was compiled with an
                                   unsupported toolchain", the kernel did not
                                   run, and the memory kept its fill pattern

That is the same sentence the assistant's engine aborted on at every launch on
that machine. `cuobjdump --list-elf` over the shipped binary listed sm_86,
sm_89, sm_120a and sm_121a and no sm_75; `--list-ptx` listed sm_75 and sm_80.
The build had succeeded and nothing noticed, because nothing asked the binary
what it contained.

A distribution pins its toolkit and its driver separately, so a driver older
than the toolkit is the normal case rather than the exception. Two things now
prevent the shape:

  * configure() refuses any `-virtual` token outright, with the reason;
  * do_install() asks each staged binary, through cuobjdump, for the
    architectures it carries, and refuses to seal the archive unless every
    declared generation is among them.

This test drives both against the recipe as it stands, with cuobjdump stubbed,
so the gate is exercised without a GPU, a toolkit or an hour of compiling.
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RECIPE_DIR = REPO / "packages" / "compute" / "llama-cpp-cuda"
PACKAGE_YML = RECIPE_DIR / "package.yml"
BUILD_SH = RECIPE_DIR / "build.sh"

# The 2026-09-17 machine measurement, as a fixture: what the shipped binary
# carried, against what the recipe now declares.
SHIPPED_BINARY_CARRIED = "sm_86 sm_89 sm_120a sm_121a"


def _declared_targets():
    for line in PACKAGE_YML.read_text().splitlines():
        if line.startswith("gpu_targets:"):
            return line.split(":", 1)[1].strip().strip('"')
    raise AssertionError("the recipe declares no gpu_targets")


class TheDeclaredTargetList(unittest.TestCase):
    def test_the_parser_grammar_accepts_every_declared_token(self):
        import sys
        sys.path.insert(0, str(REPO / "igos-build"))
        import parser as igos_parser
        token_re = igos_parser._GPU_TARGET_TOKEN_RE
        for token in _declared_targets().split(";"):
            with self.subTest(token=token):
                self.assertTrue(token_re.fullmatch(token.strip()),
                                f"the grammar rejects {token!r}")

    def test_no_declared_target_is_ptx_only(self):
        declared = _declared_targets()
        self.assertNotIn("-virtual", declared,
                         "a -virtual token promises a card the driver may not "
                         "be able to keep the promise for")

    def test_every_declared_target_carries_compiled_kernels(self):
        for token in _declared_targets().split(";"):
            with self.subTest(token=token):
                self.assertTrue(token.strip().endswith("-real"),
                                f"{token!r} is not declared -real")

    def test_turing_is_declared_because_it_is_the_toolkit_floor(self):
        # CUDA 13 removed everything older; 7.5 is the oldest generation this
        # engine can be built for, and it is the one the defect was found on.
        self.assertIn("75-real", _declared_targets())

    def test_the_recipe_states_the_age_limit(self):
        text = PACKAGE_YML.read_text()
        self.assertIn("Maxwell, Pascal, and Volta", text,
                      "the recipe must state which generations CUDA 13 dropped")
        self.assertIn("Vulkan", text,
                      "the recipe must say where older cards are served")


class _RecipeShell:
    """Runs one function lifted from the recipe's build.sh, with stubs."""

    def __init__(self, func_name):
        body = re.search(rf"^{func_name}\(\) \{{.*?^\}}",
                         BUILD_SH.read_text(), re.S | re.M)
        assert body, f"{func_name} not found in {BUILD_SH}"
        self.body = body.group(0)

    def run(self, preamble, call):
        script = f"set -u\n{preamble}\n{self.body}\n{call}\n"
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write(script)
            path = f.name
        try:
            return subprocess.run(["bash", path], capture_output=True, text=True)
        finally:
            Path(path).unlink()


class TheDeviceCodeGate(unittest.TestCase):
    """llama_assert_device_code, with cuobjdump stubbed to report a fixture."""

    def setUp(self):
        self.fn = _RecipeShell("llama_assert_device_code")
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.stub = Path(self._td.name)
        (self.stub / "bin").mkdir()
        cuobjdump = self.stub / "bin" / "cuobjdump"
        # $2 is the binary; its CONTENT is the architecture list to report.
        cuobjdump.write_text('#!/bin/bash\ncat "$2"\n')
        cuobjdump.chmod(0o755)

    def _check(self, carried, targets, with_cuobjdump=True):
        if not with_cuobjdump:
            (self.stub / "bin" / "cuobjdump").unlink()
        binary = self.stub / "fakebin"
        binary.write_text("".join(
            f"ELF file {i}: x.{i}.{a}.cubin\n"
            for i, a in enumerate(carried.split(), 1)))
        preamble = f'cuda_toolkit_root() {{ echo "{self.stub}"; }}'
        return self.fn.run(
            preamble,
            f'llama_assert_device_code "{binary}" "{targets}"')

    def test_all_declared_generations_present_passes(self):
        declared = _declared_targets()
        carried = " ".join("sm_" + t.split("-")[0] for t in declared.split(";"))
        r = self._check(carried, declared)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[device-code]", r.stdout)

    def test_the_shipped_binary_is_refused_against_the_new_list(self):
        # The exact 2026-09-17 shape: four architectures compiled, Turing only
        # as PTX, against a list that declares twelve.
        r = self._check(SHIPPED_BINARY_CARRIED, _declared_targets())
        self.assertEqual(r.returncode, 1)
        self.assertIn("sm_75", r.stderr)
        self.assertIn("Refusing to seal the archive", r.stderr)

    def test_one_missing_generation_is_named(self):
        r = self._check("sm_75 sm_80", "75-real;80-real;86-real")
        self.assertEqual(r.returncode, 1)
        self.assertIn("sm_86", r.stderr)

    def test_the_architecture_specific_suffix_is_matched_exactly(self):
        self.assertEqual(self._check("sm_120a sm_121a",
                                     "120a-real;121a-real").returncode, 0)
        # A plain sm_120 does NOT satisfy 120a: the FP4 instructions are not
        # forwards compatible, which is why the recipe declares the 'a' form.
        self.assertEqual(self._check("sm_120", "120a-real").returncode, 1)

    def test_a_binary_with_no_device_code_is_refused(self):
        r = self._check("", "75-real")
        self.assertEqual(r.returncode, 1)
        self.assertIn("(none)", r.stdout + r.stderr)

    def test_an_absent_cuobjdump_refuses_rather_than_passing(self):
        r = self._check("sm_75", "75-real", with_cuobjdump=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot verify", r.stderr)


class TheConfigureTimeRefusal(unittest.TestCase):
    """The `-virtual` refusal, lifted out of configure() as its own case."""

    def _decide(self, targets):
        script = (
            'set -u\n'
            f'CUDA_ARCHS="{targets}"\n'
            'case ";${CUDA_ARCHS};" in\n'
            r'    *-virtual\;*) echo REFUSED; exit 1 ;;' + '\n'
            'esac\n'
            'echo accepted\n'
        )
        return subprocess.run(["bash", "-c", script],
                              capture_output=True, text=True)

    def test_the_recipes_own_pattern_is_the_one_tested(self):
        # If configure()'s pattern is ever edited, this test must be edited
        # with it — so it is pinned rather than paraphrased.
        self.assertIn(r'*-virtual\;*)', BUILD_SH.read_text())

    def test_a_virtual_token_anywhere_in_the_list_is_refused(self):
        for targets in ("75-virtual;80-real",
                        "86-real;89-virtual",
                        "75-real;80-virtual;86-real"):
            with self.subTest(targets=targets):
                self.assertEqual(self._decide(targets).returncode, 1)

    def test_an_all_real_list_is_accepted(self):
        for targets in ("75-real;80-real", "120a-real;121a-real",
                        _declared_targets()):
            with self.subTest(targets=targets):
                self.assertEqual(self._decide(targets).returncode, 0)


if __name__ == "__main__":
    unittest.main()
