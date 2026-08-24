# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The in-tree unit copies must document what the recipe actually installs.

`intergen/data/intergen.service` and `intergen/data/com.intergenos.InterGen.service`
are not packaged. The recipe writes both units to their real locations from
heredocs and, in the same place, excludes the tree copies from the package with
the stated reason that they "document shipped content".

A copy that documents shipped content and does not match it is worse than no copy
at all: the next person to work on the privileged boundary reads the tree, sees a
unit with no hardening in it, and reasons about a system that does not exist.

This test holds the two to their stated job by comparing each tree copy against
the heredoc the recipe emits for the same destination. It is a byte comparison on
purpose — a unit is a contract, and "close enough" is how a hardening directive
goes missing from a document that claims to describe one.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RECIPE = _REPO_ROOT / "packages" / "ai" / "intergen" / "build.sh"

# destination written by the recipe -> the tree copy that documents it
_PAIRS = {
    "/usr/lib/systemd/user/intergen.service":
        _REPO_ROOT / "intergen" / "data" / "intergen.service",
    "/usr/share/dbus-1/services/com.intergenos.InterGen.service":
        _REPO_ROOT / "intergen" / "data" / "com.intergenos.InterGen.service",
}


def _heredoc_for(recipe_text: str, destination: str) -> str:
    """Return the heredoc body the recipe installs at `destination`.

    Matches the recipe's own shape: an `install ... /dev/stdin "<dest>" << 'TAG'`
    line, then the body, then a line that is exactly the tag.
    """
    pattern = re.compile(
        r"install\s+-Dm\d+\s+/dev/stdin\s+\"\$\{DESTDIR\}"
        + re.escape(destination)
        + r"\"\s*<<\s*'(?P<tag>[A-Z0-9_]+)'\n(?P<body>.*?)\n(?P=tag)\n",
        re.DOTALL,
    )
    match = pattern.search(recipe_text)
    if match is None:
        raise AssertionError(
            f"the recipe no longer installs {destination} from a heredoc; this "
            f"test's premise changed and the test must be re-derived, not relaxed"
        )
    return match.group("body") + "\n"


class ShippedUnitReferenceCopyTests(unittest.TestCase):

    def setUp(self):
        self.assertTrue(_RECIPE.is_file(), f"recipe not found at {_RECIPE}")
        self.recipe_text = _RECIPE.read_text(encoding="utf-8")

    def test_every_reference_copy_matches_what_the_recipe_installs(self):
        for destination, tree_copy in _PAIRS.items():
            with self.subTest(destination=destination):
                self.assertTrue(
                    tree_copy.is_file(),
                    f"{tree_copy} is missing; the recipe excludes it from the "
                    f"package on the stated grounds that it documents "
                    f"{destination}",
                )
                emitted = _heredoc_for(self.recipe_text, destination)
                documented = tree_copy.read_text(encoding="utf-8")
                self.assertEqual(
                    documented, emitted,
                    f"{tree_copy.relative_to(_REPO_ROOT)} does not match the unit "
                    f"the recipe installs at {destination}",
                )

    def test_the_documented_user_unit_carries_its_hardening(self):
        """A directly stated floor, so a future edit cannot quietly produce a
        copy that parses but describes an unhardened service."""
        documented = _PAIRS["/usr/lib/systemd/user/intergen.service"].read_text(
            encoding="utf-8")
        for directive in (
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "CapabilityBoundingSet=",
            "SystemCallFilter=",
            "RestrictAddressFamilies=",
        ):
            with self.subTest(directive=directive):
                self.assertIn(directive, documented)

    def test_the_recipe_still_excludes_both_copies_from_the_package(self):
        """The comparison above is only meaningful while the copies are
        reference material rather than packaged files."""
        self.assertRegex(
            self.recipe_text,
            r"_excl_data=\"[^\"]*intergen\.service[^\"]*\"",
        )
        self.assertRegex(
            self.recipe_text,
            r"_excl_data=\"[^\"]*com\.intergenos\.InterGen\.service[^\"]*\"",
        )


if __name__ == "__main__":
    unittest.main()
