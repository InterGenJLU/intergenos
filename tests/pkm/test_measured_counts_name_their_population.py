# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A measured number in a comment names the set it counted.

Three places in the tree justify the cache-naming scheme with the same
figure — "all 1,126 entries measured 2026-08-21" — and none of them said
WHICH 1,126 things were counted or how a reader could count them again. A
number whose population is not stated cannot be checked, and a reader who
cannot check it has to take it on faith; that is the class of claim the
evidence rules exist to remove.

Each site must therefore carry, beside the figure:

  - the population: the served signed repository index (InterGenOS.db) and
    the fact that the count is its whole entry set, not a subset;
  - the property being counted: every entry's `filename` field is exactly
    <name>-<version>.igos.tar.gz, carrying no release;
  - a re-derivation a reader can run.

Re-derived 2026-09-22 against the index a fleet machine had synced
(generated 2026-09-03): 1,126 entries, 1,126 of them with a release-less
filename, 0 otherwise. A shape-matching regular expression is NOT the way
to count them — seventeen entries carry a date or hyphenated upstream
version (dialog-1.3-20260107, re2-2025-11-05, imagemagick-7.1.2-13) whose
tail reads as a release suffix. The comparison against
<name>-<version>.igos.tar.gz is exact and has no such trap, and the
comments say so, because the next reader will reach for the regex first.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every site that states the figure. A new one joins this list.
SITES = [
    REPO_ROOT / "pkm" / "repo.py",
    REPO_ROOT / "pkm" / "cli.py",
    REPO_ROOT / "tests" / "pkm" / "test_upgrade_rehash_threading.py",
]

FIGURE = "1,126"


def _paragraph_around(text, needle):
    """The comment block the figure sits in — from the nearest blank-ish
    boundary above it to the one below, which is what a reader sees."""
    i = text.index(needle)
    start = text.rfind("\n\n", 0, i)
    end = text.find("\n\n", i)
    return text[(0 if start < 0 else start):(len(text) if end < 0 else end)]


def test_every_site_that_states_the_figure_is_covered():
    """The list above is the whole population of sites, not a sample."""
    found = [p for p in REPO_ROOT.glob("pkm/*.py")
             if FIGURE in p.read_text(encoding="utf-8")]
    found += [p for p in REPO_ROOT.glob("tests/pkm/*.py")
              if FIGURE in p.read_text(encoding="utf-8")
              and p.name != Path(__file__).name]
    assert sorted(found) == sorted(SITES), (
        "a site states the figure and is not in SITES: "
        f"{sorted(str(p) for p in found)}")


@pytest.mark.parametrize("path", SITES, ids=lambda p: p.name)
def test_the_figure_names_its_population(path):
    text = path.read_text(encoding="utf-8")
    assert FIGURE in text
    block = _paragraph_around(text, FIGURE)
    # WHICH set was counted.
    assert "InterGenOS.db" in block, (
        f"{path}: the count does not name the index it counted")
    # WHAT was counted about each member.
    assert "<name>-<version>.igos.tar.gz" in block, (
        f"{path}: the count does not state the property it measured")
    # HOW a reader counts them again.
    assert "re-derive" in block.lower() or "recount" in block.lower(), (
        f"{path}: the count offers the reader no way to derive it again")


@pytest.mark.parametrize("path", SITES, ids=lambda p: p.name)
def test_the_figure_warns_off_the_shape_regex(path):
    """The trap that would make a re-derivation wrong is named where the
    re-derivation is offered."""
    block = _paragraph_around(path.read_text(encoding="utf-8"), FIGURE)
    assert "seventeen" in block.lower() or "17 " in block, (
        f"{path}: the seventeen date- and hyphen-versioned entries that a "
        f"shape match misreads are not named")
