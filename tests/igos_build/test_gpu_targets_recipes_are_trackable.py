#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A recipe that declares gpu_targets is watched by the release auto-bump.

WHY THIS FILE EXISTS. `gpu_targets` is the declared GPU architecture list; the
builder interpolates it into the compiler's target argument, so it decides
which kernels the package emits. It was folded into the release fingerprint on
2026-09-18 — but folding a field into the fingerprint only matters for packages
the bump tool looks at in the first place, and the tool's trackability
predicate looked only for first-party content: an unpinned source, a declared
source_tree, or a file of our own in the recipe directory.

MEASURED on the composed tooling chain (tree 0f362577e, 2026-09-19): 23 recipes
declare gpu_targets and the predicate accepted 6 of them. The other 17 — every
ROCm math library, the two AI frameworks, MIOpen, composable-kernel — pin an
upstream tarball and ship no file of our own, so rewriting their architecture
list moved no release and reached no installed machine. The fingerprint could
see the field; the tool never asked those packages for a fingerprint.

So the declaration itself confers trackability: a recipe that declares
gpu_targets is tracked whether or not it pins upstream. The count below is a
FLOOR, re-derived from the tree rather than hardcoded from the measurement, so
adding a target-sensitive package cannot quietly drop out of the set.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

_spec = importlib.util.spec_from_file_location(
    "bump_changed_releases", REPO_ROOT / "scripts" / "bump-changed-releases.py")
bump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump)

# The floor is the count measured when this rule landed. It is asserted as a
# minimum, never as an equality: a new target-sensitive package must raise the
# number, and a tree that somehow answered with fewer is a tree where the
# enumeration itself broke.
_GPU_TARGETS_RECIPE_FLOOR = 23


def _gpu_targets_recipes():
    """Every package.yml under packages/ that declares a top-level gpu_targets.

    Read as text, on purpose: this is the enumeration the test is asking the
    tree for, and it must not depend on the same parser the predicate uses.
    """
    out = []
    for yml in sorted((REPO_ROOT / "packages").rglob("package.yml")):
        for line in yml.read_text().splitlines():
            if line.startswith("gpu_targets"):
                out.append(yml)
                break
    return out


class GpuTargetsRecipesAreTrackable(unittest.TestCase):

    def test_the_tree_still_holds_the_target_sensitive_set(self):
        """The enumeration itself is proven before anything is asserted of it —
        a silent zero here would make every other assertion in this file pass."""
        recipes = _gpu_targets_recipes()
        self.assertGreaterEqual(
            len(recipes), _GPU_TARGETS_RECIPE_FLOOR,
            f"only {len(recipes)} recipes declare gpu_targets; the floor when "
            f"this rule landed was {_GPU_TARGETS_RECIPE_FLOOR}. Either the "
            f"field was renamed or the enumeration is reading the wrong tree.")

    def test_every_gpu_targets_recipe_is_trackable(self):
        """The release tool must ask each of these packages for a fingerprint."""
        untracked = []
        for yml in _gpu_targets_recipes():
            pkg = bump.parse_template(yml)
            if not bump._is_trackable(pkg):
                untracked.append(str(yml.parent.relative_to(REPO_ROOT)))
        self.assertEqual(
            [], untracked,
            "these recipes declare a GPU architecture list that the compiler "
            "consumes, yet the release auto-bump does not watch them — an edit "
            "to the list would move no release and reach no installed "
            f"machine: {untracked}")

    def test_the_declaration_alone_is_enough(self):
        """Stated as a property, not as a list: a package that pins an upstream
        tarball, declares no source_tree and ships no first-party file is NOT
        trackable without the declaration, and IS trackable with it."""
        recipes = _gpu_targets_recipes()
        pinned_only = []
        for yml in recipes:
            pkg = bump.parse_template(yml)
            has_pin = any(getattr(s, "sha256", None) for s in (pkg.source or []))
            if has_pin and not getattr(pkg, "source_tree", None) \
                    and not bump.sibling_shipped_bytes(pkg):
                pinned_only.append((yml, pkg))
        self.assertTrue(
            pinned_only,
            "no recipe in the tree exercises the new clause (every gpu_targets "
            "package now carries first-party content), so this test would "
            "prove nothing — re-derive the premise before trusting it.")
        for yml, pkg in pinned_only:
            with self.subTest(recipe=str(yml.parent.relative_to(REPO_ROOT))):
                self.assertTrue(
                    bump._is_trackable(pkg),
                    "trackability here rests on the gpu_targets declaration "
                    "alone; the predicate did not accept it.")
                self.assertTrue(
                    getattr(pkg, "gpu_targets", None),
                    "the parser did not expose the declaration the predicate "
                    "is supposed to be reading.")


if __name__ == "__main__":
    unittest.main()
