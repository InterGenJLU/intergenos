# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every perl recipe that installs via perl's `make install` strips perllocal.pod.

perllocal.pod is ExtUtils::MakeMaker's install bookkeeping: it is appended per
module install at a SINGLE shared path (usr/lib/perl5/<ver>/<arch>/perllocal.pod),
so every perl package's DESTDIR captures its own divergent copy of the same path.
Packaging that shared, mutable log makes it a permanent verify-honesty hazard —
a co-owned path with per-package divergent checksums (one owner's retained bytes
modified-flag for every other owner) — and pruning one recorder deletes it out
from under the rest. The cross-distro disposition (Arch / LFS-family practice) is
that installer bookkeeping is never packaged: each recipe strips perllocal.pod
from DESTDIR before manifest + archive.

This gate DERIVES the owner set (perl modules that install via ExtUtils::MakeMaker,
plus the perl interpreter itself) rather than hardcoding it, so a NEW perl module
recipe added without the strip fails here. Toolchain-tier temporaries are excluded
(they install to the toolchain root, not a packaged DESTDIR, and are replaced in
the final system).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES = REPO_ROOT / "packages"

# A removal of perllocal.pod: either `find … -name perllocal.pod -delete` or an
# `rm …perllocal.pod`. Kept flexible so a recipe may word the strip either way.
_STRIP_RE = re.compile(
    r"(-name\s+perllocal\.pod\s+-delete|rm\b[^\n]*perllocal\.pod)")


def _perllocal_owner_recipes():
    """Recipes whose perl `make install` writes perllocal.pod: ExtUtils::MakeMaker
    modules (Makefile.PL / Build.PL) plus the perl interpreter (perl-core).
    Toolchain-tier temporaries are excluded — not shipped, not a packaged DESTDIR."""
    owners = []
    for build_sh in sorted(PACKAGES.glob("*/*/build.sh")):
        tier = build_sh.parts[len(PACKAGES.parts)]
        if tier == "toolchain":
            continue
        pkg = build_sh.parent.name
        text = build_sh.read_text()
        is_makemaker = "Makefile.PL" in text or "Build.PL" in text
        is_perl_core = pkg == "perl-core"
        if is_makemaker or is_perl_core:
            owners.append((pkg, build_sh, text))
    return owners


class PerllocalStrippedTest(unittest.TestCase):
    def test_owner_set_is_nonempty(self):
        # Guard against a vacuous pass (a broken glob would make every assertion
        # below trivially true). The known owners are perl-core + four modules.
        owners = _perllocal_owner_recipes()
        self.assertGreaterEqual(
            len(owners), 5,
            f"expected >=5 perllocal-owner recipes, found {[o[0] for o in owners]}")

    def test_every_owner_strips_perllocal(self):
        missing = [pkg for pkg, _, text in _perllocal_owner_recipes()
                   if not _STRIP_RE.search(text)]
        self.assertEqual(
            missing, [],
            "perl recipes that install via `make install` must strip "
            f"perllocal.pod from DESTDIR; these do not: {missing}")


if __name__ == "__main__":
    unittest.main()
