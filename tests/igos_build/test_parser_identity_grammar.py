"""Regression tests for the package name/version lexical grammar.

name and version are interpolated into work/staging/log/manifest/archive
paths, several of which are recursively deleted before rebuild — a
traversal-shaped name like '../sources' must die at parse time, never
reach a path join.
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
name: {name}
version: "{version}"
release: 1
description: grammar test package
license: GPL-3.0-or-later
source: []
build_style: custom
tier: core
"""


def _parse(name="demo", version="1.0"):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "package.yml"
        path.write_text(_TEMPLATE.format(name=name, version=version))
        return parse_template(path)


class TestIdentityGrammar(unittest.TestCase):
    def test_normal_identity_parses(self):
        pkg = _parse("gtk4-layer-shell", "1.0.8")
        self.assertEqual(pkg.name, "gtk4-layer-shell")
        self.assertEqual(pkg.version, "1.0.8")

    def test_corpus_shapes_parse(self):
        for name in ("perl-file-fcntllock", "lib32-mesa", "python3", "gcc_pass1"):
            _parse(name, "2.40_rc1+git")

    def test_traversal_name_rejected(self):
        for bad in ("../sources", "..", "a/b", "/etc", ".hidden", "a b"):
            with self.assertRaises((TemplateError, Exception), msg=bad):
                pkg = _parse(bad)
                # If parse survived, the grammar failed.
                raise AssertionError(f"grammar accepted malicious name {bad!r}")

    def test_traversal_version_rejected(self):
        for bad in ("../1.0", "1.0/evil", ".1", "1 0"):
            with self.assertRaises((TemplateError, Exception), msg=bad):
                pkg = _parse("demo", bad)
                raise AssertionError(f"grammar accepted malicious version {bad!r}")


if __name__ == "__main__":
    unittest.main()
