"""Security-bearing booleans parse strictly (review finding H10).

bool() coercion made any non-empty YAML value truthy — a quoted "false"
FLIPPED the flag it was trying to clear: `generated: "false"` waived the
sha pin, `iso_include: "false"` shipped a mirror-only package,
`direct_install`/`skip_tracking` strings rerouted the tracking pipeline.
All four now reject non-bool values with a TemplateError, matching the
installer_hooks pattern that was already strict.
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

GOOD_SHA = "a" * 64


def _write_template(tmp: Path, extra_yaml: str = "",
                    source_yaml: str | None = None) -> Path:
    p = tmp / "package.yml"
    source = source_yaml if source_yaml is not None else (
        f"source:\n  - url: https://x/y.tar.gz\n    sha256: {GOOD_SHA}\n")
    p.write_text(
        "name: demo\nversion: '1.0'\nrelease: 1\n"
        "description: d\nlicense: MIT\nbuild_style: custom\n"
        + source + extra_yaml)
    return p


class TestStrictBooleans(unittest.TestCase):
    def _parse(self, extra_yaml="", source_yaml=None):
        with tempfile.TemporaryDirectory() as td:
            return parse_template(
                _write_template(Path(td), extra_yaml, source_yaml))

    def test_generated_string_false_rejected(self):
        # The dangerous shape: "false" is truthy, so the old coercion set
        # generated=True and WAIVED the sha-pin requirement.
        src = "source:\n  - url: https://x/y.tar.gz\n    generated: 'false'\n"
        with self.assertRaises(TemplateError):
            self._parse(source_yaml=src)

    def test_generated_true_bool_passes(self):
        src = "source:\n  - url: file://gen.tar.xz\n    generated: true\n"
        pkg = self._parse(source_yaml=src)
        self.assertTrue(pkg.source[0].generated)

    def test_iso_include_string_rejected(self):
        with self.assertRaises(TemplateError):
            self._parse("iso_include: 'false'\n")

    def test_iso_include_int_rejected(self):
        with self.assertRaises(TemplateError):
            self._parse("iso_include: 1\n")

    def test_iso_include_bool_passes(self):
        pkg = self._parse("iso_include: false\n")
        self.assertFalse(pkg.iso_include)

    def test_iso_include_absent_applies_tier_default(self):
        self.assertTrue(self._parse().iso_include)          # core → ships
        self.assertFalse(self._parse("tier: extra\n").iso_include)  # mirror
        self.assertFalse(self._parse("tier: compute\n").iso_include)  # mirror
        # toolchain = build intermediates (pass*/tmp twins): neither ISO nor
        # mirror — the SBOM gate's 2026-08-06 first release firing counted
        # all 25 as shipped-with-no-archive under the old default.
        self.assertFalse(self._parse("tier: toolchain\n").iso_include)

    def test_direct_install_string_rejected(self):
        with self.assertRaises(TemplateError):
            self._parse("direct_install: 'false'\n")

    def test_skip_tracking_string_rejected(self):
        with self.assertRaises(TemplateError):
            self._parse("skip_tracking: 'true'\n")

    def test_direct_install_and_skip_tracking_bools_pass(self):
        pkg = self._parse("direct_install: true\nskip_tracking: false\n")
        self.assertTrue(pkg.direct_install)
        self.assertFalse(pkg.skip_tracking)


if __name__ == "__main__":
    unittest.main()
