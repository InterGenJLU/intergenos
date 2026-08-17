"""Unknown package.yml top-level keys are a hard parse failure (review finding M2).

A typo'd control field ('direct_instal', 'verify_path') used to warn on
stderr and run default semantics — invisible in a long build log. It now
raises TemplateError at parse time, naming every unknown key.
"""

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from parser import (  # noqa: E402  (path manipulation must precede import)
    KNOWN_FIELDS,
    TemplateError,
    parse_template,
)

GOOD_SHA = "a" * 64

BASE = (
    "name: demo\nversion: '1.0'\nrelease: 1\n"
    "description: d\nlicense: MIT\nbuild_style: custom\n"
    f"source:\n  - url: https://x/y.tar.gz\n    sha256: {GOOD_SHA}\n")


class TestUnknownKeysRejected(unittest.TestCase):
    def _parse(self, text):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "package.yml"
            p.write_text(text)
            return parse_template(p)

    def test_typoed_control_field_rejected(self):
        with self.assertRaises(TemplateError) as ctx:
            self._parse(BASE + "direct_instal: true\n")
        self.assertIn("direct_instal", str(ctx.exception))

    def test_typoed_verify_paths_rejected(self):
        # The original catching incident's shape: snappy's verify_paths
        # silently dropped.
        with self.assertRaises(TemplateError) as ctx:
            self._parse(BASE + "verify_path:\n  - /usr/bin/demo\n")
        self.assertIn("verify_path", str(ctx.exception))

    def test_all_unknown_keys_named_at_once(self):
        with self.assertRaises(TemplateError) as ctx:
            self._parse(BASE + "bogus_one: 1\nbogus_two: 2\n")
        msg = str(ctx.exception)
        self.assertIn("bogus_one", msg)
        self.assertIn("bogus_two", msg)

    def test_known_fields_still_parse(self):
        pkg = self._parse(BASE + "tier: base\nhomepage: https://x\n")
        self.assertEqual(pkg.tier, "base")

    def test_external_reader_fields_accepted(self):
        # Fields consumed by scripts outside parse_template stay legal —
        # they are registered in KNOWN_FIELDS, not dropped.
        self.assertIn("verify_paths", KNOWN_FIELDS)
        self.assertIn("content_hash", KNOWN_FIELDS)
        pkg = self._parse(BASE + "verify_paths:\n  - /usr/bin/demo\n")
        self.assertEqual(pkg.name, "demo")


if __name__ == "__main__":
    unittest.main()
