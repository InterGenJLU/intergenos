# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-2.3 — phrasing families: expand a class seed into its wording siblings.

A class is not one sentence. The same request arrives colloquial, imperative,
polite, emotional, or sloppy (typos), and the class invariant must hold across
every wording. The phase-1 sweep surfaced the opposite — behavior that FLIPS on
phrasing (a tool-grounding request answered from the model's head under one
wording, grounded under another). A phrasing family turns that into graded
coverage: each alternate wording of a turn becomes its own sibling scenario
carrying the SAME assertions, so a wording that breaks the invariant fails
visibly instead of hiding behind the one canonical sentence.

Expansion varies ONE turn at a time (the turn's ``user`` is swapped for a
phrasing while every other turn stays at its base wording). That yields
``sum(len(phrasings_k))`` variants — linear, not the combinatorial product — and
each variant isolates the effect of a single turn's wording, which is what the
brittleness finding is about. A scenario with no phrasings passes through
unchanged, so expansion is safe to run over the whole battery.
"""

from __future__ import annotations

from copy import deepcopy

from intergen.tests.scenario.schema import Scenario, Turn

# Appended to the base id to mark a variant; the base id stays a clean prefix so
# the comparator can still group a family (join on id.split(VARIANT_SEP)[0]).
VARIANT_SEP = "#"


def _turn_key(scenario: Scenario, index: int) -> str:
    """A stable, human-readable key for a turn within its scenario (t1, t2, …).
    Single-turn scenarios — the common phrasing-family shape — collapse to the
    phrasing label alone at the call site, keeping variant ids short."""
    return f"t{index + 1}"


def _variant(scenario: Scenario, turn_index: int, phrasing_text: str,
             variant_id: str, phrasing_label: str) -> Scenario:
    """A deep copy of ``scenario`` with turn ``turn_index`` re-worded, a new id,
    and provenance tags — same assertions, same markers, same axes."""
    clone = deepcopy(scenario)
    clone.id = variant_id
    clone.turns[turn_index].user = phrasing_text
    # The alternate wordings are consumed into the base user text; drop them on
    # the variant so a re-expansion is idempotent.
    clone.turns[turn_index].phrasings = []
    clone.tags = list(scenario.tags) + ["variant", f"phrasing:{phrasing_label}"]
    return clone


def expand_scenario(scenario: Scenario) -> list[Scenario]:
    """Expand one scenario into ``[base, *variants]`` (base first, then variants
    in turn-then-phrasing order). A scenario with no phrasings returns ``[base]``.
    Variant ids are ``{base_id}#{turn_key}-{phrasing_label}`` (the turn_key is
    omitted for a single-turn scenario, since there is no ambiguity)."""
    has_any = any(turn.phrasings for turn in scenario.turns)
    if not has_any:
        return [scenario]

    single_turn = len(scenario.turns) == 1
    variants: list[Scenario] = []
    seen_ids: set[str] = {scenario.id}
    for i, turn in enumerate(scenario.turns):
        for phrasing in turn.phrasings:
            key = phrasing.label if single_turn else f"{_turn_key(scenario, i)}-{phrasing.label}"
            variant_id = f"{scenario.id}{VARIANT_SEP}{key}"
            if variant_id in seen_ids:  # loader guards label collisions; belt-and-braces
                raise ValueError(
                    f"phrasing family for {scenario.id!r} produced a duplicate "
                    f"variant id {variant_id!r}")
            seen_ids.add(variant_id)
            variants.append(_variant(scenario, i, phrasing.text, variant_id, phrasing.label))
    return [scenario, *variants]


def expand_families(scenarios: list[Scenario]) -> list[Scenario]:
    """Expand every scenario's phrasing family, preserving order (each base is
    immediately followed by its variants). Scenarios without phrasings pass
    through unchanged, so this is safe to run over the whole battery before a
    graded run."""
    out: list[Scenario] = []
    for scenario in scenarios:
        out.extend(expand_scenario(scenario))
    return out
