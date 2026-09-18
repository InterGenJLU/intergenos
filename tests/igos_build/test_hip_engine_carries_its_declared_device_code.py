#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The HIP engine must carry device code for every architecture it declares.

Nothing verified the declaration. packages/compute/llama-cpp-hip/package.yml
declares gpu_targets, build.sh passes it to cmake as -DGPU_TARGETS, and the
recipe writes it into a record beside the binary — but the binary was never
asked what it actually contains. A target dropped by a flag change, a toolchain
that skipped one, or a declaration widened without the compile following would
all have shipped green, and the failure on the far end is not graceful: this
engine segfaults at model load on an architecture it has no kernels for.

do_install() now asks each staged binary and refuses to seal the archive if a
declared architecture is absent.

HOW IT READS, measured 2026-09-17 against the installed engine on a machine
running ROCm 7.2.4:

  roc-obj-ls                    shipped, but does not run at all on this
                                distribution — it is a perl script requiring
                                File::Which, which the project's perl does not
                                carry, so it aborts before reading anything.
  llvm-objdump --offloading     works, and listed gfx1100, gfx1102 and gfx1201 —
                                but EXTRACTS every bundle entry as a file beside
                                the input, which against a staged binary would
                                write the extracted code objects into DESTDIR and
                                ship them.
  llvm-objcopy --dump-section   reads only, writes nothing beside the binary, and
                                scopes the scan to .hip_fatbin. Same three
                                architectures, 125 bundle entries each.

The section scoping is what keeps this from being a substring match on an
executable: a string constant elsewhere in the binary cannot be mistaken for a
code object.

These tests drive the gate with llvm-objcopy stubbed, so it is exercised without
a GPU, a ROCm toolchain, or an hour of compiling.
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RECIPE_DIR = REPO / "packages" / "compute" / "llama-cpp-hip"
PACKAGE_YML = RECIPE_DIR / "package.yml"
BUILD_SH = RECIPE_DIR / "build.sh"

# What the installed engine carried on 2026-09-17, as a fixture.
SHIPPED_BINARY_CARRIED = ["gfx1100", "gfx1102", "gfx1201"]


def _declared_targets():
    for line in PACKAGE_YML.read_text().splitlines():
        if line.startswith("gpu_targets:"):
            return line.split(":", 1)[1].strip().strip('"')
    raise AssertionError("the recipe declares no gpu_targets")


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


class TheDeclaration(unittest.TestCase):

    def test_every_declared_token_is_a_gfx_architecture(self):
        for token in _declared_targets().split(";"):
            with self.subTest(token=token):
                self.assertRegex(token.strip(), r"^gfx[0-9a-z]+$")

    def test_the_gate_is_called_for_every_binary_the_package_declares(self):
        # verify_paths names the binaries this package ships; each one must be
        # asked. A binary that ships unasked is the gap this gate exists to
        # close.
        declared_binaries = [
            line.strip().lstrip("- ").rsplit("/", 1)[-1]
            for line in PACKAGE_YML.read_text().splitlines()
            if line.strip().startswith("- /opt/rocm/bin/")
        ]
        self.assertTrue(declared_binaries)
        text = BUILD_SH.read_text()
        do_install = text[text.index("do_install() {"):]
        for binary in declared_binaries:
            with self.subTest(binary=binary):
                self.assertIn(binary, do_install)

    def test_the_recipe_states_the_age_limit_and_where_older_cards_go(self):
        text = PACKAGE_YML.read_text()
        self.assertIn("RDNA2 (gfx1030)", text,
                      "the recipe must say which generation is the floor")
        self.assertIn("Vulkan", text,
                      "the recipe must say where an older AMD card is served")
        self.assertIn("compatibility-matrix", text,
                      "the floor is AMD's statement, so the recipe cites it")

    def test_the_recipe_states_what_bounds_the_list(self):
        # The next person to widen this line needs to know, in the file they
        # are editing, that the math libraries bound it — not the compiler.
        text = PACKAGE_YML.read_text()
        for phrase in ("rocBLAS", "hipBLAS", "math libraries widen"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_the_recipe_records_why_gfx1102_is_declared(self):
        # It is not on AMD's list and it is deliberate. Without this recorded
        # in the file, a future reader comparing the line to AMD's matrix would
        # "correct" a working card away.
        text = PACKAGE_YML.read_text()
        self.assertIn("gfx1102 IS DELIBERATE", text)
        self.assertIn("gfx1102", _declared_targets())

    def test_the_scan_is_scoped_to_the_section_that_holds_the_bundles(self):
        body = _RecipeShell("llama_assert_device_code").body
        self.assertIn("--dump-section=.hip_fatbin=-", body,
                      "an unscoped scan of an executable would match a string "
                      "constant as if it were a code object")


class TheParserGrammarAgainstTheRocmTargetList(unittest.TestCase):
    """What gpu_targets: is allowed to say, against what ROCm 7.2.4 offers.

    The concrete architecture names come from the installed toolchain's own
    table, captured 2026-09-17:
        /opt/rocm/lib/llvm/bin/clang -target amdgcn-amd-amdhsa -mcpu=help
    """

    # Every concrete (non-"generic") AMDGPU target that table lists.
    ROCM_724_CONCRETE_TARGETS = [
        "gfx1010", "gfx1011", "gfx1012", "gfx1013",
        "gfx1030", "gfx1031", "gfx1032", "gfx1033", "gfx1034", "gfx1035",
        "gfx1036",
        "gfx1100", "gfx1101", "gfx1102", "gfx1103",
        "gfx1150", "gfx1151", "gfx1152", "gfx1153",
        "gfx1200", "gfx1201", "gfx1250", "gfx1251",
        "gfx600", "gfx601", "gfx602",
        "gfx700", "gfx701", "gfx702", "gfx703", "gfx704", "gfx705",
        "gfx801", "gfx802", "gfx803", "gfx805", "gfx810",
        "gfx900", "gfx902", "gfx904", "gfx906", "gfx908", "gfx909",
        "gfx90a", "gfx90c", "gfx942", "gfx950",
    ]

    # The family targets from the same table. They are NOT accepted by the
    # grammar, and that is recorded here rather than left to be discovered by
    # whoever first writes one into a recipe.
    ROCM_724_GENERIC_TARGETS = [
        "gfx9-generic", "gfx9-4-generic", "gfx10-1-generic",
        "gfx10-3-generic", "gfx11-generic", "gfx12-generic",
    ]

    def setUp(self):
        import sys
        sys.path.insert(0, str(REPO / "igos-build"))
        import parser as igos_parser
        self.token_re = igos_parser._GPU_TARGET_TOKEN_RE

    def test_every_currently_declared_token_is_accepted(self):
        for token in _declared_targets().split(";"):
            with self.subTest(token=token):
                self.assertTrue(self.token_re.fullmatch(token.strip()))

    def test_every_concrete_rocm_target_is_accepted(self):
        # So widening the declaration to any card ROCm 7.2.4 compiles for is a
        # decision about kernels and build time, never a grammar edit.
        for target in self.ROCM_724_CONCRETE_TARGETS:
            with self.subTest(target=target):
                self.assertTrue(self.token_re.fullmatch(target),
                                f"the grammar rejects {target}")

    def test_the_family_targets_are_not_accepted_and_this_is_the_record(self):
        # gfx11-generic and its siblings carry a hyphen, which the token
        # grammar does not admit. Declaring one today would fail template
        # parsing, so a recipe that wants a family target needs the grammar
        # widened deliberately first.
        for target in self.ROCM_724_GENERIC_TARGETS:
            with self.subTest(target=target):
                self.assertIsNone(self.token_re.fullmatch(target))


class TheDeviceCodeGate(unittest.TestCase):
    """llama_assert_device_code, with llvm-objcopy stubbed to report a fixture."""

    def setUp(self):
        self.fn = _RecipeShell("llama_assert_device_code")
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.rocm = Path(self._td.name) / "rocm"
        (self.rocm / "lib" / "llvm" / "bin").mkdir(parents=True)
        self.objcopy = self.rocm / "lib" / "llvm" / "bin" / "llvm-objcopy"
        # The real invocation is:
        #   llvm-objcopy --dump-section=.hip_fatbin=- <binary> /dev/null
        # so $2 is the binary. Its CONTENT stands in for the section's bytes.
        self.objcopy.write_text('#!/bin/bash\n'
                                'f="$2"\n'
                                '[ -s "$f" ] || exit 1\n'
                                'cat "$f"\n')
        self.objcopy.chmod(0o755)

    def _section_bytes(self, carried, repeats=125):
        # The real section repeats each bundle entry id once per translation
        # unit — 125 times on the engine measured. Reproduced so the gate is
        # shown to deduplicate rather than to have been handed a clean list.
        out = []
        for _ in range(repeats):
            for arch in carried:
                out.append(f"__CLANG_OFFLOAD_BUNDLE__hipv4-amdgcn-amd-amdhsa--{arch}")
        return "\n".join(out) + "\n" if carried else ""

    def _check(self, carried, targets, with_objcopy=True, repeats=125):
        if not with_objcopy:
            self.objcopy.unlink()
        binary = Path(self._td.name) / "fakebin"
        binary.write_text(self._section_bytes(carried, repeats))
        preamble = f'ROCM_PATH="{self.rocm}"'
        return self.fn.run(preamble,
                           f'llama_assert_device_code "{binary}" "{targets}"')

    def test_all_declared_architectures_present_passes(self):
        declared = _declared_targets()
        r = self._check(declared.split(";"), declared)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[device-code]", r.stdout)

    def test_the_repeated_entries_are_reported_once_each(self):
        declared = _declared_targets()
        r = self._check(declared.split(";"), declared)
        reported = r.stdout.split("carries:", 1)[1].split()
        self.assertEqual(sorted(reported), sorted(declared.split(";")))

    def test_a_widened_declaration_the_build_did_not_follow_is_refused(self):
        # The shape this gate exists for: the declaration grows, the compile
        # does not, and the binary still contains only what it always did.
        widened = ";".join(SHIPPED_BINARY_CARRIED + ["gfx1103"])
        r = self._check(SHIPPED_BINARY_CARRIED, widened)
        self.assertEqual(r.returncode, 1)
        self.assertIn("gfx1103", r.stderr)
        self.assertIn("Refusing to seal the archive", r.stderr)

    def test_one_missing_architecture_is_named(self):
        r = self._check(["gfx1100", "gfx1201"], "gfx1100;gfx1102;gfx1201")
        self.assertEqual(r.returncode, 1)
        self.assertIn("gfx1102", r.stderr)

    def test_a_binary_with_no_device_code_is_refused(self):
        r = self._check([], "gfx1100")
        self.assertEqual(r.returncode, 1)
        self.assertIn("(none)", r.stdout + r.stderr)
        self.assertIn("no AMD device code at all", r.stderr)

    def test_an_absent_objcopy_refuses_rather_than_passing(self):
        r = self._check(SHIPPED_BINARY_CARRIED, "gfx1100", with_objcopy=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot verify", r.stderr)

    def test_a_longer_architecture_name_does_not_satisfy_a_shorter_one(self):
        # gfx1103 is not gfx110; a prefix match would pass a card that has no
        # code, which is exactly the promise this gate checks.
        self.assertEqual(self._check(["gfx1103"], "gfx1100").returncode, 1)
        self.assertEqual(self._check(["gfx1100"], "gfx1100").returncode, 0)


class TheMathKernelCheck(unittest.TestCase):
    """llama_assert_math_kernels, against a stand-in ROCm prefix.

    The engine's matrix multiplies resolve into rocBLAS through hipBLAS, and
    both ship per-architecture kernel files generated from their OWN declared
    target list. Carrying a code object for a card the math libraries were not
    built for is the same broken promise one level down.

    The file names are the real ones, read from an installed ROCm 7.2.4 on
    2026-09-17: rocblas/library holds Kernels.so-000-gfx1100.hsaco and
    TensileLibrary_..._fallback_gfx1100.hsaco, 96 files naming gfx1100, 96
    naming gfx1102 and 56 naming gfx1201; hipblaslt/library holds 95, 95 and
    297.
    """

    REAL_FILE_SHAPES = [
        "Kernels.so-000-{gfx}.hsaco",
        "TensileLibrary_Type_4xi8I_HPA_Contraction_l_Ailk_Bjlk_Cijk_Dijk_"
        "fallback_{gfx}.hsaco",
    ]

    def setUp(self):
        self.fn = _RecipeShell("llama_assert_math_kernels")
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.rocm = Path(self._td.name) / "rocm"

    def _populate(self, lib, archs):
        d = self.rocm / "lib" / lib / "library"
        d.mkdir(parents=True, exist_ok=True)
        # One architecture-free file too, as the real directories have: a
        # listing that matched everything would prove nothing.
        (d / "TensileLibrary_Type_4xi8I_HPA_Contraction_fallback.dat").touch()
        for arch in archs:
            for shape in self.REAL_FILE_SHAPES:
                (d / shape.format(gfx=arch)).touch()

    def _check(self, targets):
        return self.fn.run(f'ROCM_PATH="{self.rocm}"',
                           f'llama_assert_math_kernels "{targets}"')

    def test_every_declared_architecture_has_kernels_passes(self):
        declared = _declared_targets()
        for lib in ("rocblas", "hipblaslt"):
            self._populate(lib, declared.split(";"))
        r = self._check(declared)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[math-kernels] rocblas carries:", r.stdout)
        self.assertIn("[math-kernels] hipblaslt carries:", r.stdout)

    def test_a_card_the_math_libraries_lack_is_refused_and_named(self):
        # The shape this exists for: the engine's list is widened to a card
        # rocBLAS was never built for.
        for lib in ("rocblas", "hipblaslt"):
            self._populate(lib, SHIPPED_BINARY_CARRIED)
        r = self._check(";".join(SHIPPED_BINARY_CARRIED + ["gfx1103"]))
        self.assertEqual(r.returncode, 1)
        self.assertIn("gfx1103", r.stderr)
        self.assertIn("Refusing to seal", r.stderr)

    def test_the_second_library_is_checked_too(self):
        # rocblas complete, hipblaslt short: a check that stopped at the first
        # library would pass this.
        self._populate("rocblas", SHIPPED_BINARY_CARRIED)
        self._populate("hipblaslt", ["gfx1100"])
        r = self._check(";".join(SHIPPED_BINARY_CARRIED))
        self.assertEqual(r.returncode, 1)
        self.assertIn("hipblaslt", r.stderr)

    def test_a_missing_library_directory_refuses_rather_than_passing(self):
        self._populate("rocblas", SHIPPED_BINARY_CARRIED)
        r = self._check(";".join(SHIPPED_BINARY_CARRIED))
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot verify", r.stderr)

    def test_an_empty_library_directory_refuses_rather_than_passing(self):
        for lib in ("rocblas", "hipblaslt"):
            (self.rocm / "lib" / lib / "library").mkdir(parents=True)
        r = self._check("gfx1100")
        self.assertEqual(r.returncode, 1)
        self.assertIn("(none)", r.stdout + r.stderr)

    def test_the_architecture_free_files_do_not_satisfy_anything(self):
        # The .dat fallback names no architecture; only the .hsaco files do.
        for lib in ("rocblas", "hipblaslt"):
            d = self.rocm / "lib" / lib / "library"
            d.mkdir(parents=True)
            (d / "TensileLibrary_Type_4xi8I_HPA_Contraction_fallback.dat").touch()
        r = self._check("gfx1100")
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
