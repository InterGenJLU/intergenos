"""`redistributable: false` on a source entry: parse, validate, and enforce.

Some inputs may be fetched and built against but not republished by us. The
first is the NVIDIA CUDA toolkit runfile that compute/llama-cpp-cuda compiles
against: nvcc is not redistributable under NVIDIA's CUDA EULA.

Marking the entry is what keeps its bytes out of a corresponding-source
archive. These tests pin the declaration end of that: the flag defaults to
true, is strict-bool like `generated`, and the two combinations that would
make it meaningless or unenforceable are declaration errors rather than
silently-accepted noise.

The consuming end — scripts/build-source-archives.py refusing to bundle a
withheld input and writing a pointer note in its place — is pinned by
tests/repo-publish/test_source_archive_redistributable.py.
"""

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from parser import (  # noqa: E402  (path manipulation must precede import)
    TemplateError,
    parse_template,
)

GOOD_SHA = "b" * 64


def _parse(source_yaml: str):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "package.yml"
        p.write_text(
            "name: demo\nversion: '1.0'\nrelease: 1\n"
            "description: d\nlicense: MIT\nbuild_style: custom\n"
            + source_yaml)
        return parse_template(p)


class TestSourceRedistributable(unittest.TestCase):
    def test_defaults_true(self):
        pkg = _parse(f"source:\n  - url: https://x/y.tar.gz\n"
                     f"    sha256: {GOOD_SHA}\n")
        self.assertTrue(pkg.source[0].redistributable)

    def test_false_parses_on_a_pinned_source(self):
        pkg = _parse(f"source:\n  - url: https://v/vendor.run\n"
                     f"    sha256: {GOOD_SHA}\n"
                     f"    extract: false\n"
                     f"    redistributable: false\n")
        self.assertFalse(pkg.source[0].redistributable)
        self.assertFalse(pkg.source[0].extract)

    def test_per_entry_not_per_package(self):
        # The real shape: llama-cpp-cuda's source[0] is MIT llama.cpp and its
        # source[1] is NVIDIA's toolkit. One archive, two answers.
        pkg = _parse(f"source:\n"
                     f"  - url: https://x/llama.tar.gz\n"
                     f"    sha256: {GOOD_SHA}\n"
                     f"  - url: https://v/vendor.run\n"
                     f"    sha256: {'c' * 64}\n"
                     f"    redistributable: false\n")
        self.assertTrue(pkg.source[0].redistributable)
        self.assertFalse(pkg.source[1].redistributable)

    def test_strict_bool(self):
        # The quoted-"false" class: a string is truthy, so a coercing parser
        # would read `redistributable: "false"` as PERMISSION TO REPUBLISH —
        # the flag failing in the exact direction that causes the harm.
        for bad in ('"false"', "'no'", "0", "[]"):
            with self.subTest(value=bad):
                with self.assertRaises(TemplateError):
                    _parse(f"source:\n  - url: https://v/v.run\n"
                           f"    sha256: {GOOD_SHA}\n"
                           f"    redistributable: {bad}\n")

    def test_unpinned_withheld_source_rejected(self):
        # Excluding an input by identity requires an identity. Without a hash
        # the pointer note could not tell anyone which bytes to fetch, and the
        # exclusion could not be checked.
        with self.assertRaises(TemplateError):
            _parse("source:\n  - url: https://v/v.run\n"
                   "    generated: true\n"
                   "    redistributable: false\n")

    def test_generated_source_cannot_be_withheld(self):
        # A generated tarball is built from this repository's own content.
        # Declaring it non-redistributable is a contradiction, not a policy.
        with self.assertRaises(TemplateError):
            _parse("source:\n  - url: https://x/gen.tar.gz\n"
                   "    generated: true\n"
                   "    redistributable: false\n")


if __name__ == "__main__":
    unittest.main()
