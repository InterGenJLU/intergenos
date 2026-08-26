#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A toolchain recipe never carries the name of a package that ships.

WHY THIS TEST HAD TO EXIST. `scripts/build-source-archives.py` names every
corresponding-source archive `<ships_as or name>-<version>-<release>` and
writes it into one flat directory, so two recipes that resolve to the same
name-version-release write the same file and the last one wins. Measured on
2026-08-25 against the real tree: `packages/core/m4-core` (ships_as: m4) and
`packages/toolchain/m4` both claimed `m4-1.4.21-1.igos.src.tar.gz`, and
`packages/core/ncurses-core` (ships_as: ncurses) and
`packages/toolchain/ncurses` both claimed `ncurses-6.6-1.igos.src.tar.gz`.
The toolchain recipe sorted last, so the published archive described the
toolchain recipe rather than the one that built the binary: for m4 that meant
an archive with no `build.sh` at all, because that recipe carries none — ten
of the 28 toolchain recipes do not, their build steps living inline in
`scripts/temp-tools-build.sh`; for ncurses it meant the toolchain recipe's
build script standing in for the core recipe's. A third pair, glibc, shared
the ship name and was kept apart only by release numbers that happened to
differ.

The rename that resolved it is not self-enforcing: nothing stopped the next
toolchain recipe from being added under a plain upstream name. This file is
that enforcement. It reads the REAL packages tree, because the claim under
test is about the real recipes.

Two claims:

  1. Every `tier: toolchain` recipe that is an intermediate stage of a
     package that ships -- its name is that shipped name, or that shipped
     name followed by a hyphen -- is named `<shipped>-tmp` or
     `<shipped>-pass<N>`. Those are the two forms
     `check-source-correspondence.py`'s `resolve_toolchain_twin()` searches
     for when it proves a recipe-less Chapter-8 binary's source through its
     toolchain twin, and the digit form is what
     `check-corpus-correspondence.py`'s never-publish `INTERMEDIATE_RE`
     accepts; requiring the intersection keeps one name readable by both.
     A name equal to the shipped name is the collision above; a name like
     `<shipped>-toolchain` collides with nothing but is invisible to both
     readers, so neither is allowed. A toolchain recipe that is no shipped
     package's stage (libstdcpp, linux-headers) needs no discriminator and
     is not asked for one.

  2. A recipe whose name is deliberately NOT its upstream project's name --
     it carries `ships_as:`, or its name ends in `-tmp`, `-pass<N>` or
     `-bootstrap` -- never substitutes `${name}` into a source url or
     filename. `${name}` expands to the RECIPE name (the builder caches
     downloads under it, and `upstream_tarball_name()` in the generator
     resolves the stored filename the same way), so a renamed recipe that
     kept `${name}` would point at a URL upstream does not serve and look
     for a tarball nobody downloaded. `packages/toolchain/glibc` carried
     exactly that url before the rename.

Each claim has a fixture negative control, so a green run means the tree
conforms rather than the check having found nothing to look at. Nothing here
writes to the tree, reads the network, or needs privilege.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = REPO_ROOT / "packages"

# Names that mean "this recipe is not called what upstream calls itself". The
# source correspondence gate accepts `<base>-tmp` and anything starting
# `<base>-pass`; the corpus gate's never-publish pattern requires
# `-pass<digits>` or `-bootstrap`. This set is the union of what they read.
RENAMED_SUFFIX_RE = re.compile(r"-(?:tmp|pass\d+|bootstrap)$")


def _recipes(packages_dir: Path) -> list[dict]:
    """Every parsed recipe under a packages tree, with its directory.

    A recipe that cannot be parsed is a finding for the gates that own that
    check (preflight-tier-coverage.py fails on a malformed manifest); here it
    is skipped so this file reports only on the claim it is about.
    """
    out = []
    for pkg_yml in sorted(packages_dir.glob("*/*/package.yml")):
        try:
            meta = yaml.safe_load(pkg_yml.read_text())
        except yaml.YAMLError:
            continue
        if isinstance(meta, dict) and meta.get("name"):
            meta["_dir"] = pkg_yml.parent
            out.append(meta)
    return out


def _shipped_names(recipes: list[dict]) -> set[str]:
    """The names packages actually publish under.

    A `tier: toolchain` recipe publishes no binary -- `toolchain-build.sh` and
    `temp-tools-build.sh` emit no `.igos.tar.gz` at all -- so the shipped set
    is everything else, taken as `ships_as` where a recipe declares one.
    """
    return {
        (r.get("ships_as") or r["name"])
        for r in recipes
        if r.get("tier") != "toolchain"
    }


def unresolvable_toolchain_twins(packages_dir: Path) -> list[tuple[str, str, str]]:
    """(name, shipped_name, dir) for each toolchain stage named unresolvably.

    A toolchain recipe is treated as a stage of a shipped package when its
    name is that shipped name or begins with it followed by a hyphen. Where
    more than one shipped name qualifies, the longest wins, so `util-linux-tmp`
    is read as a stage of `util-linux` rather than of `util`.
    """
    recipes = _recipes(packages_dir)
    shipped = _shipped_names(recipes)
    findings = []
    for r in recipes:
        if r.get("tier") != "toolchain":
            continue
        name = r["name"]
        stage_of = [s for s in shipped if name == s or name.startswith(s + "-")]
        if not stage_of:
            continue
        base = max(stage_of, key=len)
        if not re.fullmatch(rf"{re.escape(base)}-(?:tmp|pass\d+)", name):
            findings.append((name, base, str(r["_dir"])))
    return findings


def renamed_recipes_substituting_name(packages_dir: Path) -> list[tuple[str, str, str]]:
    """(name, field, value) for each renamed recipe still using ${name}."""
    findings = []
    for r in _recipes(packages_dir):
        name = r["name"]
        if not (r.get("ships_as") or RENAMED_SUFFIX_RE.search(name)):
            continue
        for entry in r.get("source") or []:
            if not isinstance(entry, dict):
                continue
            for field in ("url", "filename"):
                value = entry.get(field)
                if value and "${name}" in str(value):
                    findings.append((name, field, str(value)))
    return findings


# --------------------------------------------------------------------------
# The real tree
# --------------------------------------------------------------------------

def test_every_toolchain_stage_of_a_shipped_package_is_named_resolvably():
    findings = unresolvable_toolchain_twins(PACKAGES_DIR)
    assert findings == [], (
        "a toolchain recipe that stages a package which ships is named so that "
        "it either collides with that package's source archive or cannot be "
        "resolved as its twin:\n"
        + "\n".join(
            f"  {d}: name '{n}' stages shipped '{b}' "
            f"— rename it '{b}-tmp' (or '{b}-pass<N>') and move every "
            f"reference in the same commit"
            for n, b, d in findings
        )
    )


def test_no_renamed_recipe_substitutes_its_own_name_into_a_source_url():
    findings = renamed_recipes_substituting_name(PACKAGES_DIR)
    assert findings == [], (
        "a recipe whose name is not its upstream project's name expands "
        "${name} into a source location, which resolves to the recipe name:\n"
        + "\n".join(f"  {n}: {field} = {value}" for n, field, value in findings)
    )


# --------------------------------------------------------------------------
# Negative controls — the checks above must be able to fail
# --------------------------------------------------------------------------

def _write_recipe(packages_dir: Path, tier: str, dirname: str, body: str) -> None:
    d = packages_dir / tier / dirname
    d.mkdir(parents=True)
    (d / "package.yml").write_text(body)


@pytest.fixture()
def fixture_tree(tmp_path: Path) -> Path:
    packages = tmp_path / "packages"
    _write_recipe(
        packages, "core", "widget-core",
        'name: widget-core\nships_as: widget\nversion: "1.0"\nrelease: 1\n'
        "tier: core\n"
        "source:\n  - url: https://example.invalid/widget-${version}.tar.xz\n",
    )
    _write_recipe(
        packages, "toolchain", "widget-tmp",
        'name: widget-tmp\nversion: "1.0"\nrelease: 1\ntier: toolchain\n'
        "source:\n  - url: https://example.invalid/widget-${version}.tar.xz\n",
    )
    return packages


def test_the_conforming_fixture_tree_reports_nothing(fixture_tree: Path):
    assert unresolvable_toolchain_twins(fixture_tree) == []
    assert renamed_recipes_substituting_name(fixture_tree) == []


def test_a_planted_shadowing_toolchain_recipe_is_reported(fixture_tree: Path):
    _write_recipe(
        fixture_tree, "toolchain", "widget",
        'name: widget\nversion: "1.0"\nrelease: 1\ntier: toolchain\n'
        "source:\n  - url: https://example.invalid/widget-${version}.tar.xz\n",
    )
    findings = unresolvable_toolchain_twins(fixture_tree)
    assert [f[0] for f in findings] == ["widget"]
    assert findings[0][1] == "widget"


def test_a_planted_non_resolvable_discriminator_is_reported(fixture_tree: Path):
    """`widget-toolchain` collides with nothing, and neither correspondence
    gate can resolve it either — the suffix has to be one they read."""
    _write_recipe(
        fixture_tree, "toolchain", "widget-toolchain",
        'name: widget-toolchain\nversion: "1.0"\nrelease: 1\ntier: toolchain\n'
        "source:\n  - url: https://example.invalid/widget-${version}.tar.xz\n",
    )
    findings = unresolvable_toolchain_twins(fixture_tree)
    assert [f[0] for f in findings] == ["widget-toolchain"]
    assert findings[0][1] == "widget"


def test_a_planted_name_substitution_in_a_renamed_recipe_is_reported(fixture_tree: Path):
    _write_recipe(
        fixture_tree, "toolchain", "gadget-tmp",
        'name: gadget-tmp\nversion: "2.0"\nrelease: 1\ntier: toolchain\n'
        "source:\n  - url: https://example.invalid/${name}/${name}-${version}.tar.xz\n",
    )
    findings = renamed_recipes_substituting_name(fixture_tree)
    assert findings == [
        ("gadget-tmp", "url",
         "https://example.invalid/${name}/${name}-${version}.tar.xz")
    ]


def test_a_plain_recipe_may_substitute_its_own_name(fixture_tree: Path):
    """`packages/core/bash` is named what upstream calls it, so ${name} in its
    url resolves to 'bash' and is correct — the check must not report it."""
    _write_recipe(
        fixture_tree, "core", "gadget",
        'name: gadget\nversion: "2.0"\nrelease: 1\ntier: core\n'
        "source:\n  - url: https://example.invalid/${name}/${name}-${version}.tar.xz\n",
    )
    assert renamed_recipes_substituting_name(fixture_tree) == []
