# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Host-side validation + matrix-cell ledger for the Phase-2 scenario corpus.

Loads every corpus scenario through the REAL loader (so a scenario that would
not load in the harness fails here first), then reports the as-built composition
ledger — category x behavior-axis x quadrant x posture, plus the mandatory-class
tallies (never-list two-beat, the four domain-signal quadrants, the IG-S-12 offer
rows, tone-flip phrasing families) and the coverage-backlog cells closed. Run:

    python3 -m intergen.tests.scenario.corpus.validate_corpus
"""

from __future__ import annotations

import collections
from pathlib import Path

from intergen.tests.scenario.loader import load_scenarios

_CORPUS = Path(__file__).resolve().parent
_SEEDS = _CORPUS.parent / "seeds"


def main() -> int:
    scenarios = load_scenarios(_CORPUS)   # raises loudly on any schema violation
    n = len(scenarios)
    cat = collections.Counter(s.category for s in scenarios)
    axis = collections.Counter(a for s in scenarios for a in s.axis)
    posture = collections.Counter(p for s in scenarios for p in s.postures)
    turns = sum(len(s.turns) for s in scenarios)
    phrasings = sum(len(t.phrasings) for s in scenarios for t in s.turns)
    tags = collections.Counter(t for s in scenarios for t in s.tags)
    quad = {q: tags.get(f"quadrant:{q}", 0) for q in ("Q-A", "Q-B", "Q-C", "Q-D")}
    ggap = {g: tags.get(f"ggap:{g}", 0) for g in ("G1", "G2", "G3", "G4", "G5")}
    never = sum(1 for s in scenarios if "class:never-list" in s.tags)
    band = {b: tags.get(f"band:{b}", 0) for b in ("B1", "B2", "B3")}
    # F-mandate coverage from the fringe-reconciliation sweep: which F# fringe
    # findings have at least one NEW row explicitly tagged to them (the machine-
    # checkable half of the per-F ledger; pre-existing coverage is reported in the
    # delivery ledger, since old rows are not retro-tagged).
    fmandate = {f"F{i}": tags.get(f"fmandate:F{i}", 0) for i in range(1, 24)}
    fcovered = sorted((k for k, v in fmandate.items() if v), key=lambda s: int(s[1:]))

    print(f"CORPUS LOADS CLEAN — {n} scenarios, {turns} turns, {phrasings} phrasings")
    print(f"\ncategory:  {dict(cat)}")
    print(f"axis:      {dict(axis)}")
    print(f"posture:   {dict(posture)}")
    print(f"quadrants: {quad}")
    print(f"G-gaps:    {ggap}")
    print(f"never-list two-beat scenarios: {never}")
    print(f"first-boot tier bands: {band}")
    print(f"F-mandate rows (new, tagged): {{{', '.join(f'{k}:{fmandate[k]}' for k in fcovered)}}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
