"""gpu_targets field: parse, validate, and export to the build env.

Target-sensitive compute packages (rocblas, rocwmma, llama-cpp-hip,
llama-cpp-cuda) DECLARE their GPU ISA set in package.yml — the chroot has no
GPU, so declaration is the only honest source, and the recipes consume
${IGOS_GPU_TARGETS:?} fail-closed. These tests pin the whole path: a valid
declaration parses and round-trips verbatim, a malformed one dies at parse
time (never reaches a compiler as a silently-wrong target list), and the
builder exports the value to the build environment exactly when declared.

Two vendor spellings share the one field, because they are the same
declaration: AMD gfx identifiers (ROCm) and NVIDIA compute-capability
tokens (CUDA, the CMAKE_CUDA_ARCHITECTURES vocabulary).
"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
_parser_mod = importlib.import_module("igos-build.parser")
parse_template = _parser_mod.parse_template
TemplateError = _parser_mod.TemplateError

_TEMPLATE = """\
name: demo
version: "1.0"
release: 1
description: gpu_targets test package
license: GPL-3.0-or-later
source: []
build_style: custom
tier: compute
{gpu_targets_line}"""


def _parse(gpu_targets_line=""):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "package.yml"
        path.write_text(_TEMPLATE.format(gpu_targets_line=gpu_targets_line))
        return parse_template(path)


class TestGpuTargetsParse(unittest.TestCase):
    def test_declared_set_round_trips(self):
        pkg = _parse('gpu_targets: "gfx1100;gfx1102;gfx1201"\n')
        self.assertEqual(pkg.gpu_targets, "gfx1100;gfx1102;gfx1201")

    def test_single_target_parses(self):
        self.assertEqual(_parse('gpu_targets: "gfx1201"\n').gpu_targets,
                         "gfx1201")

    def test_xnack_feature_suffix_parses(self):
        pkg = _parse('gpu_targets: "gfx90a:xnack+;gfx90a:xnack-"\n')
        self.assertEqual(pkg.gpu_targets, "gfx90a:xnack+;gfx90a:xnack-")

    def test_absent_is_none(self):
        self.assertIsNone(_parse().gpu_targets)

    def test_cuda_compute_capability_set_round_trips(self):
        # The exact set llama-cpp-cuda declares: PTX floor at Turing (the
        # driver package's hardware floor), real SASS for the consumer
        # generations, architecture-specific 'a' for Blackwell.
        decl = "75-virtual;80-virtual;86-real;89-real;120a-real;121a-real"
        self.assertEqual(_parse(f'gpu_targets: "{decl}"\n').gpu_targets, decl)

    def test_cuda_bare_capability_parses(self):
        # No -real/-virtual suffix = build both PTX and SASS.
        self.assertEqual(_parse('gpu_targets: "86"\n').gpu_targets, "86")

    def test_cuda_arch_specific_suffix_parses(self):
        # 90a / 120a: architecture-specific features (not forwards-compatible).
        self.assertEqual(_parse('gpu_targets: "90a;120a"\n').gpu_targets,
                         "90a;120a")

    def test_mixed_vendor_tokens_parse(self):
        # The grammar is per-token, so it does not itself forbid a mixed
        # declaration; a recipe builds for exactly one backend, and which
        # tokens are meaningful is the recipe's business, not the parser's.
        pkg = _parse('gpu_targets: "gfx1100;86-real"\n')
        self.assertEqual(pkg.gpu_targets, "gfx1100;86-real")

    def test_sm_prefixed_capability_rejected(self):
        # nvcc's `sm_86` spelling is NOT the cmake spelling; accepting it
        # would hand cmake a token it rejects late, in the build, instead of
        # here at parse time.
        with self.assertRaises(TemplateError):
            _parse('gpu_targets: "sm_86"\n')

    def test_single_digit_capability_rejected(self):
        with self.assertRaises(TemplateError):
            _parse('gpu_targets: "8"\n')

    def test_unknown_capability_qualifier_rejected(self):
        with self.assertRaises(TemplateError):
            _parse('gpu_targets: "86-fake"\n')

    def test_empty_string_rejected(self):
        with self.assertRaises(TemplateError):
            _parse('gpu_targets: ""\n')

    def test_non_gfx_token_rejected(self):
        with self.assertRaises(TemplateError):
            _parse('gpu_targets: "pascal;gfx1100"\n')

    def test_non_string_rejected(self):
        with self.assertRaises(TemplateError):
            _parse('gpu_targets: [gfx1100, gfx1201]\n')

    def test_shell_metacharacter_rejected(self):
        # The value lands in a build-env variable a recipe interpolates into
        # cmake -DGPU_TARGETS= — a token like `gfx1100;$(rm -rf /)` must die
        # at parse time, never reach a shell.
        with self.assertRaises(TemplateError):
            _parse('gpu_targets: "gfx1100;$(rm -rf /)"\n')


class TestGpuTargetsBuildEnv(unittest.TestCase):
    def _env_for(self, pkg):
        _builder_mod = importlib.import_module("igos-build.builder")
        ex = _builder_mod.BuildExecutor.__new__(_builder_mod.BuildExecutor)
        ex.system_root = Path("/tmp/igos-test-root")
        ex.target_triple = "x86_64-pc-linux-gnu"
        ex.jobs = 1
        ex.sources_dir = Path("/tmp/igos-test-sources")
        ex.patches_dir = Path("/tmp/igos-test-patches")
        ex.tracked = False
        return ex.build_env(pkg)

    def test_declared_exports_env(self):
        pkg = _parse('gpu_targets: "gfx1100;gfx1201"\n')
        env = self._env_for(pkg)
        self.assertEqual(env.get("IGOS_GPU_TARGETS"), "gfx1100;gfx1201")

    def test_undeclared_exports_nothing(self):
        pkg = _parse()
        env = self._env_for(pkg)
        self.assertNotIn("IGOS_GPU_TARGETS", env)


if __name__ == "__main__":
    unittest.main()
