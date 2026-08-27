# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A scenario that means the same thing on every tier must say so.

The grader skips a scenario whose ``postures`` do not include the tier being
driven, so a scenario that declares ``["2B-locked"]`` alone is invisible to a 9B
or 35B run. When every ungated assertion in it is tier-independent
(:mod:`intergen.tests.scenario.tier_independence`), that invisibility is not a
statement about the machine — it is a gap in what the corpus measures.

These tests fail if the corpus reopens that gap.
"""
from __future__ import annotations

import glob
import json
import os

import pytest

from intergen.tests.scenario import tier_independence as ti

CORPUS_DIR = os.path.join(os.path.dirname(__file__), "corpus")


def _scenarios():
    for path in sorted(glob.glob(os.path.join(CORPUS_DIR, "*.json"))):
        data = json.load(open(path, encoding="utf-8"))
        scns = data if isinstance(data, list) else data.get("scenarios", [])
        for s in scns:
            if isinstance(s, dict) and "id" in s:
                yield os.path.basename(path), s


def test_corpus_is_not_empty():
    """Guards the two tests below: a glob that matched nothing would pass them
    both while measuring nothing at all."""
    found = list(_scenarios())
    assert len(found) > 100, f"only {len(found)} scenarios found under {CORPUS_DIR}"


def test_every_assertion_type_is_classified():
    """An assertion type nobody has classified must not drift into the corpus.

    ``classify`` returns ``unknown`` rather than guessing, so an unclassified
    type would quietly make its scenario ineligible for widening and nothing
    would say so. This is the thing that says so.
    """
    unknown: dict[str, set[str]] = {}
    for filename, s in _scenarios():
        for ty in ti.unknown_assertion_types(s):
            unknown.setdefault(ty, set()).add(f"{filename}:{s['id']}")
    assert not unknown, (
        "these assertion types are not classified in tier_independence.py — "
        "add each to TIER_INDEPENDENT or TIER_DEPENDENT with its reason: "
        + "; ".join(f"{ty} (e.g. {sorted(w)[0]})" for ty, w in sorted(unknown.items()))
    )


def test_tier_independent_scenarios_declare_every_tier():
    """The rule this cut exists to hold.

    A scenario whose every ungated assertion reads a recorded decision, or a
    property every tier owes, applies to every tier — so it must declare every
    tier, or the 9B and 35B runs skip it.
    """
    offenders = []
    for filename, s in _scenarios():
        if not ti.scenario_is_tier_independent(s):
            continue
        declared = set(s.get("postures") or [])
        if declared != set(ti.ALL_POSTURES):
            offenders.append(f"{filename}:{s['id']} declares {sorted(declared) or '[]'}")
    assert not offenders, (
        f"{len(offenders)} scenario(s) are tier-independent but do not declare all "
        f"of {list(ti.ALL_POSTURES)}, so a 9B or 35B run skips them:\n  "
        + "\n  ".join(offenders[:40])
        + (f"\n  ... and {len(offenders) - 40} more" if len(offenders) > 40 else "")
    )


@pytest.mark.parametrize("atype", sorted(ti.TIER_INDEPENDENT | ti.TIER_DEPENDENT))
def test_classification_is_not_ambiguous(atype):
    """No assertion type may sit in both buckets."""
    assert not (atype in ti.TIER_INDEPENDENT and atype in ti.TIER_DEPENDENT), (
        f"{atype} is classified as both tier-independent and tier-dependent"
    )
