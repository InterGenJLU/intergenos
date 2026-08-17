#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The kernel's corresponding source must carry the config it was built from.

WHAT WAS WRONG, measured 2026-08-07 against the PUBLISHED artifact. The kernel
source archive on the mirror — linux-kernel-6.18.10-10.igos.src.tar.gz — held
the upstream tarball, its sha256, build.sh and package.yml, and nothing else.
Its own build.sh reads `cat "$frag_dir"/*.config > .config`, so the two config
fragments are what decide which drivers the kernel has, and neither was in the
archive. A recipient had everything except the one input that determines what
the shipped kernel IS, and could not reproduce it.

The mechanism to fix it already existed and could not be used:
scripts/build-source-archives.py has always documented and consumed a
`sources_extra:` field, naming the kernel config fragments as its own example,
while igos-build/parser.py did not register the key — so every recipe that
declared it died at parse time and no package ever did. A documented mechanism
that rejects its own documented use is a stub in the Rule 21 sense: it claims a
capability the tree does not have.

WHAT THESE TESTS HOLD. Registration alone is not enough, because the next
fragment added would silently not ride. So: every live fragment must be
declared, by both kernel recipes, with no path that does not exist and no glob
that would fail closed at archive time.
"""
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FRAGMENT_DIR = REPO_ROOT / "config/kernel/fragments"
KERNEL_RECIPES = (
    "packages/core/linux-kernel",
    "packages/core/linux-kernel-pass2",
)

sys.path.insert(0, str(REPO_ROOT / "igos-build"))


def parse(recipe):
    from parser import parse_template
    return parse_template(REPO_ROOT / recipe / "package.yml")


def declared(recipe):
    """sources_extra as the recipe declares it, read through the real parser's
    YAML rather than by grepping, so the test sees what the generator sees."""
    import yaml
    raw = yaml.safe_load((REPO_ROOT / recipe / "package.yml").read_text())
    return list(raw.get("sources_extra") or [])


def live_fragments():
    """The fragments the build actually reads: fragments/*.config at depth 1.

    fragments/archive/ holds retired fragments the build never touches — the
    recipe globs one level only — so they are deliberately not corresponding
    source for the shipped binary.
    """
    return sorted(p.name for p in FRAGMENT_DIR.glob("*.config"))


def test_sources_extra_is_a_registered_recipe_field():
    """The whole defect in one assertion: the field the source-archive generator
    consumes must be one the parser accepts, or no recipe can ever use it."""
    from parser import KNOWN_FIELDS
    assert "sources_extra" in KNOWN_FIELDS, (
        "scripts/build-source-archives.py reads `sources_extra:` from package.yml, "
        "but igos-build/parser.py does not register the key, so any recipe declaring "
        "it fails to parse. A consumed-but-unregistered field is unusable by design."
    )


def test_the_field_is_actually_usable_end_to_end():
    """A control on the assertion above: the real parser must accept a real
    recipe that declares it. Registration in a set proves nothing on its own."""
    for recipe in KERNEL_RECIPES:
        pkg = parse(recipe)          # raises TemplateError on an unknown key
        assert pkg.name


def test_there_is_at_least_one_live_fragment():
    """Guard against the whole suite passing because the glob found nothing."""
    assert live_fragments(), f"no *.config in {FRAGMENT_DIR}"


@pytest.mark.parametrize("recipe", KERNEL_RECIPES)
def test_every_live_fragment_is_declared(recipe):
    """Both recipes build from every fragment, so both must ship every one."""
    names = {pathlib.PurePosixPath(e).name for e in declared(recipe)}
    missing = [f for f in live_fragments() if f not in names]
    assert not missing, (
        f"{recipe}/package.yml does not declare {missing} in sources_extra. "
        "Every config/kernel/fragments/*.config is a build input, so it must ride "
        "in the corresponding-source archive or the published source cannot "
        "rebuild the published binary."
    )


@pytest.mark.parametrize("recipe", KERNEL_RECIPES)
def test_every_declared_path_exists(recipe):
    """Mirrors the generator's own fail-closed check, but in the suite rather
    than at publish time — a typo here refuses the archive during a release."""
    for entry in declared(recipe):
        assert (REPO_ROOT / entry).exists(), (
            f"{recipe} declares sources_extra entry {entry!r}, which does not exist. "
            "build-source-archives.py refuses the archive on a missing entry."
        )


@pytest.mark.parametrize("recipe", KERNEL_RECIPES)
def test_no_declared_path_is_a_glob(recipe):
    """There is no glob expansion. The generator resolves each entry with
    Path.exists(), so 'fragments/*.config' matches nothing and fails closed at
    release time — the worst moment to find out. Caught here instead."""
    for entry in declared(recipe):
        assert not any(c in entry for c in "*?["), (
            f"{recipe} declares sources_extra entry {entry!r}, which contains a glob "
            "character. sources_extra entries are literal paths; a glob resolves to "
            "nothing and refuses the archive."
        )


@pytest.mark.parametrize("recipe", KERNEL_RECIPES)
def test_retired_fragments_are_not_shipped_as_corresponding_source(recipe):
    """fragments/archive/ is retired content the build never reads. Corresponding
    source should be what the binary was built from, not everything nearby."""
    for entry in declared(recipe):
        assert "fragments/archive" not in entry, (
            f"{recipe} declares {entry!r}. The build reads fragments/*.config one "
            "level deep only; archive/ is retired and is not corresponding source."
        )
