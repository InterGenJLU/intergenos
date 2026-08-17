"""Parser tests for the ships_as field (F25 namespace wave, 2026-07-21)."""

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from parser import TemplateError, parse_template  # noqa: E402

TEMPLATE = """\
name: gcc-core
version: "15.2.0"
release: 1
description: test fixture
license: GPL-3.0-or-later
build_style: custom
tier: core
source:
- url: https://example.com/gcc-15.2.0.tar.xz
  sha256: "{sha}"
{extra}"""


def _write(tmpdir, extra=""):
    p = Path(tmpdir) / "package.yml"
    p.write_text(TEMPLATE.format(sha="0" * 64, extra=extra))
    return p


class TestShipsAsParsing(unittest.TestCase):

    def test_ships_as_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            pkg = parse_template(_write(d, "ships_as: gcc\n"))
            self.assertEqual(pkg.ships_as, "gcc")

    def test_ships_as_defaults_none(self):
        with tempfile.TemporaryDirectory() as d:
            pkg = parse_template(_write(d))
            self.assertIsNone(pkg.ships_as)

    def test_ships_as_equal_to_name_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(TemplateError) as ctx:
                parse_template(_write(d, "ships_as: gcc-core\n"))
            self.assertIn("equals name:", str(ctx.exception))

    def test_ships_as_bad_grammar_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(TemplateError):
                parse_template(_write(d, "ships_as: /usr/bin/gcc\n"))

    def test_ships_as_non_string_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(TemplateError):
                parse_template(_write(d, "ships_as: [gcc]\n"))


if __name__ == "__main__":
    unittest.main()
