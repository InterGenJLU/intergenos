# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Alignment tests for scripts/pre-squashfs-audit.py's mirror-only exemption.

The audit once exempted only EXPLICIT `iso_include: false`. It now exempts
EFFECTIVE iso_include False through effective_iso_include(), which CALLS the
parser's rule (igos-build/parser.effective_iso_include) instead of restating
it. These tests pin that the tier:extra-default population (no explicit
override — the common MIRROR case) resolves False, i.e. exempted, while a
shipping tier's default stays True and is still audited.

The tier list itself is pinned next door in
test_pre_squashfs_toolchain_tier_exemption.py, together with the requirement
that no copy of the rule lives in the audit at all.

CHANGED 2026-08-13: this file used to assert that a quoted "false" coerced to
True here, describing it as reproducing a parser footgun. That description had
gone stale — the parser rejects a non-boolean iso_include at parse time (strict
booleans, review finding H10), and the audit now refuses it the same way rather
than coercing. Coercion is the dangerous direction here: a falsy non-boolean
would have exempted a package silently, which is the one outcome an audit
against silent regressions must not produce.
"""

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "pre-squashfs-audit.py"

_spec = importlib.util.spec_from_file_location("pre_squashfs_audit", SCRIPT)
_psa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_psa)
effective_iso_include = _psa.effective_iso_include


class TestEffectiveIsoInclude(unittest.TestCase):
    def test_extra_default_now_resolves_false_exempted(self):
        # THE alignment: a tier:extra package with NO explicit iso_include is
        # effectively MIRROR-only. The old `iso_include is False` test missed
        # it; the effective rule exempts it, matching derive-iso-exclusions.
        self.assertFalse(effective_iso_include({"tier": "extra"}))

    def test_non_extra_default_stays_true_audited(self):
        for tier in ("core", "base", "desktop", "ai"):
            self.assertTrue(effective_iso_include({"tier": tier}),
                            f"{tier} default must stay iso_include True")

    def test_missing_tier_defaults_core_true(self):
        self.assertTrue(effective_iso_include({}))

    def test_explicit_false_wins_over_non_extra_tier(self):
        self.assertFalse(
            effective_iso_include({"tier": "core", "iso_include": False}))

    def test_explicit_true_wins_over_extra_tier(self):
        self.assertTrue(
            effective_iso_include({"tier": "extra", "iso_include": True}))

    def test_string_false_is_refused_exactly_as_the_parser_refuses_it(self):
        # The parser raises TemplateError on a non-boolean iso_include; the
        # audit raises the ValueError that same shared rule produces. Neither
        # one guesses. preflight-iso-closure.py also refuses the value at
        # validate time, so this is now the third place it cannot slip past.
        with self.assertRaises(ValueError):
            effective_iso_include({"tier": "core", "iso_include": "false"})


if __name__ == "__main__":
    unittest.main()
