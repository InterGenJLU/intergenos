"""Unit tests for the supersedes field handling in igos-build/parser.py.

Covers parse_template field validation (supersedes type-checking + self-edge
rejection), _validate_supersedes_no_cycles graph traversal, and
_warn_missing_supersedees no-op-on-missing-target behaviour.
"""

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from parser import (  # noqa: E402  (path manipulation must precede import)
    Dependencies,
    Package,
    TemplateError,
    _validate_supersedes_no_cycles,
    _warn_missing_supersedees,
    parse_template,
)


MINIMAL_YAML_TEMPLATE = """\
name: {name}
version: "1.0"
release: 1
description: "test package"
license: "MIT"
build_style: make
source:
  - url: "https://example.com/{name}-1.0.tar.gz"
    sha256: "0000000000000000000000000000000000000000000000000000000000000000"
{extra}
"""


def _write_template(tmp_dir: Path, name: str, extra_yaml: str = "") -> Path:
    path = tmp_dir / "package.yml"
    path.write_text(MINIMAL_YAML_TEMPLATE.format(name=name, extra=extra_yaml))
    return path


def _make_pkg(name: str, supersedes: list) -> Package:
    return Package(
        name=name,
        version="1.0",
        release=1,
        description="test",
        license="MIT",
        source=[],
        dependencies=Dependencies(),
        build_style="make",
        supersedes=supersedes,
        template_path=Path(f"/synthetic/{name}/package.yml"),
    )


class SupersedesFieldParseTests(unittest.TestCase):
    """parse_template handling of the supersedes field."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_of_strings_parses(self):
        path = _write_template(
            self.tmp_path, "p",
            extra_yaml="supersedes:\n  - pkg-a\n  - pkg-b\n",
        )
        pkg = parse_template(path)
        self.assertEqual(pkg.supersedes, ["pkg-a", "pkg-b"])

    def test_absent_field_defaults_empty(self):
        path = _write_template(self.tmp_path, "p")
        pkg = parse_template(path)
        self.assertEqual(pkg.supersedes, [])

    def test_empty_list_parses_as_empty(self):
        path = _write_template(
            self.tmp_path, "p", extra_yaml="supersedes: []\n",
        )
        pkg = parse_template(path)
        self.assertEqual(pkg.supersedes, [])

    def test_self_supersede_rejected(self):
        path = _write_template(
            self.tmp_path, "p",
            extra_yaml="supersedes:\n  - p\n",
        )
        with self.assertRaises(TemplateError) as ctx:
            parse_template(path)
        self.assertIn("cannot supersede itself", ctx.exception.message)

    def test_non_string_entry_rejected(self):
        path = _write_template(
            self.tmp_path, "p",
            extra_yaml="supersedes:\n  - 123\n  - valid\n",
        )
        with self.assertRaises(TemplateError) as ctx:
            parse_template(path)
        self.assertIn("must be a string", ctx.exception.message)

    def test_non_list_value_rejected(self):
        path = _write_template(
            self.tmp_path, "p",
            extra_yaml="supersedes: bare-string\n",
        )
        with self.assertRaises(TemplateError) as ctx:
            parse_template(path)
        self.assertIn("must be a list", ctx.exception.message)


class SupersedesCycleDetectionTests(unittest.TestCase):
    """_validate_supersedes_no_cycles three-color DFS over the graph."""

    def test_direct_cycle_raises(self):
        pkgs = [
            _make_pkg("a", ["b"]),
            _make_pkg("b", ["a"]),
        ]
        with self.assertRaises(TemplateError) as ctx:
            _validate_supersedes_no_cycles(pkgs)
        self.assertIn("cycle detected", ctx.exception.message)

    def test_indirect_cycle_raises(self):
        pkgs = [
            _make_pkg("a", ["b"]),
            _make_pkg("b", ["c"]),
            _make_pkg("c", ["a"]),
        ]
        with self.assertRaises(TemplateError) as ctx:
            _validate_supersedes_no_cycles(pkgs)
        self.assertIn("cycle detected", ctx.exception.message)

    def test_linear_chain_accepted(self):
        pkgs = [
            _make_pkg("a", ["b"]),
            _make_pkg("b", []),
        ]
        _validate_supersedes_no_cycles(pkgs)  # must not raise

    def test_diamond_supersede_accepted(self):
        pkgs = [
            _make_pkg("a", ["c"]),
            _make_pkg("b", ["c"]),
            _make_pkg("c", []),
        ]
        _validate_supersedes_no_cycles(pkgs)  # must not raise

    def test_no_packages_accepted(self):
        _validate_supersedes_no_cycles([])  # must not raise

    def test_orphan_supersedee_does_not_cycle(self):
        pkgs = [_make_pkg("a", ["nonexistent-pkg"])]
        _validate_supersedes_no_cycles(pkgs)  # must not raise


class SupersedesMissingTargetWarnTests(unittest.TestCase):
    """_warn_missing_supersedees returns warnings rather than raising."""

    def test_missing_target_returns_warning_string(self):
        pkgs = [_make_pkg("a", ["does-not-exist"])]
        warnings = _warn_missing_supersedees(pkgs)
        self.assertEqual(len(warnings), 1)
        self.assertIn("does-not-exist", warnings[0])
        self.assertIn("no package", warnings[0])

    def test_all_present_returns_empty_list(self):
        pkgs = [
            _make_pkg("a", ["b"]),
            _make_pkg("b", []),
        ]
        warnings = _warn_missing_supersedees(pkgs)
        self.assertEqual(warnings, [])

    def test_multiple_missing_returns_multiple_warnings(self):
        pkgs = [_make_pkg("a", ["missing-1", "missing-2"])]
        warnings = _warn_missing_supersedees(pkgs)
        self.assertEqual(len(warnings), 2)


if __name__ == "__main__":
    unittest.main()
