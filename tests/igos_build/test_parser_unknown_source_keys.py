# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Unknown keys INSIDE a source entry are a hard parse failure.

Top-level recipe keys have failed closed since the snappy `verify_paths`
incident (see test_parser_unknown_keys.py). Source entries did not: every key
the parser did not read was dropped in silence.

Measured 2026-08-25 across all 1174 shipped recipes and their 1305 source
entries: exactly one such key existed, `fallback_url` in
packages/core/shim-signed/package.yml. It read as a second place to obtain the
file if the first failed. Nothing in the tree ever read it, so it obtained
nothing — and the URL it named answered 404 as well. A recipe that states an
availability guarantee the build does not implement is the silent-assumption
shape this project removes: the reader believes there are two ways to get the
bytes, and there is one.

These tests make the drop impossible rather than documented.
"""
from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from parser import (  # noqa: E402  (path manipulation must precede import)
    KNOWN_SOURCE_FIELDS,
    Source,
    TemplateError,
    parse_template,
)

GOOD_SHA = "a" * 64

BASE = (
    "name: demo\nversion: '1.0'\nrelease: 1\n"
    "description: d\nlicense: MIT\nbuild_style: custom\n")


def _recipe(*source_lines):
    body = "source:\n  - url: https://x/y.tar.gz\n" \
           f"    sha256: {GOOD_SHA}\n"
    for line in source_lines:
        body += f"    {line}\n"
    return BASE + body


class TestUnknownSourceKeysRejected(unittest.TestCase):
    def _parse(self, text):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "package.yml"
            p.write_text(text)
            return parse_template(p)

    def test_the_key_that_was_being_dropped_is_now_rejected(self):
        with self.assertRaises(TemplateError) as ctx:
            self._parse(_recipe("fallback_url: https://elsewhere/y.tar.gz"))
        self.assertIn("fallback_url", str(ctx.exception))

    def test_a_typoed_pin_is_rejected_rather_than_waiving_the_pin(self):
        # `sha256sum:` instead of `sha256:` used to leave the entry with no pin
        # at all, and the missing-pin check then fired on a recipe that looked
        # pinned. Naming the typo is the useful error.
        with self.assertRaises(TemplateError) as ctx:
            self._parse(BASE + "source:\n  - url: https://x/y.tar.gz\n"
                        f"    sha256sum: {GOOD_SHA}\n")
        self.assertIn("sha256sum", str(ctx.exception))

    def test_every_unknown_key_is_named_at_once(self):
        with self.assertRaises(TemplateError) as ctx:
            self._parse(_recipe("bogus_one: 1", "bogus_two: 2"))
        msg = str(ctx.exception)
        self.assertIn("bogus_one", msg)
        self.assertIn("bogus_two", msg)

    def test_the_error_names_the_entry_and_the_supported_keys(self):
        with self.assertRaises(TemplateError) as ctx:
            self._parse(_recipe("bogus: 1"))
        msg = str(ctx.exception)
        self.assertIn("source[0]", msg)
        self.assertIn("url", msg)

    def test_every_supported_key_still_parses(self):
        pkg = self._parse(
            BASE + "source:\n  - url: https://x/y.tar.gz\n"
            f"    sha256: {GOOD_SHA}\n"
            "    filename: y.tar.gz\n"
            "    extract: false\n"
            "    redistributable: true\n")
        self.assertEqual(pkg.source[0].filename, "y.tar.gz")
        self.assertFalse(pkg.source[0].extract)

    def test_the_supported_set_matches_the_dataclass(self):
        """The list the parser checks against and the object it fills must not
        drift apart: a field added to one and not the other reintroduces the
        drop this test exists to stop."""
        self.assertEqual(
            set(KNOWN_SOURCE_FIELDS),
            {f.name for f in dataclasses.fields(Source)})


class TestShippedRecipesDeclareNoDroppedKey(unittest.TestCase):
    """The corpus itself, not a fixture — the recipes that actually ship."""

    def test_no_shipped_recipe_declares_a_key_the_parser_would_drop(self):
        offenders = {}
        ymls = sorted((REPO_ROOT / "packages").rglob("package.yml"))
        self.assertGreater(len(ymls), 100,
                           "the corpus scan found almost no recipes — the "
                           "population is wrong, so a pass would mean nothing")
        for y in ymls:
            doc = yaml.safe_load(y.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                continue
            entries = doc.get("source") or []
            if isinstance(entries, dict):
                entries = [entries]
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                unknown = sorted(set(entry) - set(KNOWN_SOURCE_FIELDS))
                if unknown:
                    offenders.setdefault(
                        str(y.relative_to(REPO_ROOT)), set()).update(unknown)
        self.assertEqual(
            offenders, {},
            f"these recipes declare source keys nothing reads: "
            f"{ {k: sorted(v) for k, v in offenders.items()} }")


if __name__ == "__main__":
    unittest.main()
