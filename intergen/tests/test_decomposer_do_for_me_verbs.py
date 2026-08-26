# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A compound "do X, then Y" system request must enter decomposition.

Ten whole-battery scenarios of the do-for-me class ("find a pdf editor and
install it", "check my disk usage and delete the temp files ...") reported no
decomposition: the turn trace carried no sub_queries, so a clause could be
answered while its sibling was silently dropped — the loss class decomposition
exists to prevent.

Root cause, measured: the module carried THREE separate action-verb lists that
had drifted apart.

  * ``_IMPERATIVE_VERBS`` — the alternation inside the conjunction SIGNALS that
    ``detect_compound`` matches, and inside the ``split_compound`` split points.
  * ``_ACTION_VERBS`` — the alternation ``count_actions`` uses.
  * a third, shorter literal list inside ``split_compound``'s comma fallback.

``_ACTION_VERBS`` already counted ``find|read|write|create|delete|update`` as
distinct actions while the conjunction alternation did not contain them, so
"look up today's weather **and write** me a note" scored two actions and was
still not a compound. Detection, splitting and counting now read ONE shared
alternation, so the three cannot drift apart again.

Tiers: decomposition detection is tier-free. ``detect_compound``,
``split_compound``, ``count_actions`` and ``_clause_has_content`` take no tier
argument at all, and the only tier-conditional code in the module —
``_TIER_THRESHOLDS`` and ``validate_decomposition_cap`` — is diagnostic-only and
gates nothing. The defect is therefore identical on the 2B, 9B and 35B tiers,
and every case here runs under all three configurations to prove it rather than
assert it.

No model is involved: these exercise the regex/table layer directly, so the
suite runs in milliseconds with nothing stubbed out that could hide a defect.
"""
import unittest

from intergen.decomposer import (
    analyze_query,
    count_actions,
    detect_compound,
    split_compound,
)
from intergen.interfaces.types import HardwareTierLevel

# Every tier the daemon configures. The defect and the fix are asserted under
# each one; a fix proven on a single tier would not be a fix of this code.
TIERS = (
    HardwareTierLevel.TIER_1,   # 2B
    HardwareTierLevel.TIER_2,   # 9B
    HardwareTierLevel.TIER_3,   # 35B
)

# The ten whole-battery do-for-me prompts, verbatim from the run's results.json
# (scenario ids WRT-do-for-me-01..10), with the sub-query count each scenario's
# decomposes_into assertion expects.
DO_FOR_ME = [
    ("WRT-do-for-me-01", "find a pdf editor and install it", 2),
    ("WRT-do-for-me-02", "find a good note-taking app and install it", 2),
    ("WRT-do-for-me-03",
     "look up today's weather and write me a note about what to wear", 2),
    ("WRT-do-for-me-04",
     "check what services are running and restart the one that's stopped", 2),
    ("WRT-do-for-me-05",
     "find out how much disk space I have and save a summary to a file", 2),
    ("WRT-do-for-me-06",
     "look up how to set up a cron job and create one for me", 2),
    ("WRT-do-for-me-07",
     "find a screenshot tool and use it to capture my screen", 2),
    ("WRT-do-for-me-08",
     "search for a file manager app, install it, and open it", 3),
    ("WRT-do-for-me-09", "check if docker is installed and if not, install it", 2),
    ("WRT-do-for-me-10",
     "check my disk usage and delete the temp files if it's over 80% full", 2),
]

# The six that produced no sub_queries at all before the fix. Kept named so a
# regression points at the exact conjunction shape that broke, not at "the
# do-for-me tests".
FAILED_AT_BASE = {
    "WRT-do-for-me-03",   # "and write"  — write absent from the conjunction list
    "WRT-do-for-me-05",   # "and save"   — save absent from every list
    "WRT-do-for-me-06",   # "and create" — create absent from the conjunction list
    "WRT-do-for-me-07",   # "and use"    — use absent from every list
    "WRT-do-for-me-09",   # "and if not, install" — conditional interposed
    "WRT-do-for-me-10",   # "and delete" — delete absent from the conjunction list
}


class DoForMeDecomposesTest(unittest.TestCase):
    """Every do-for-me prompt decomposes, on every tier."""

    def test_all_ten_decompose_on_every_tier(self):
        for sid, prompt, expected in DO_FOR_ME:
            for tier in TIERS:
                with self.subTest(scenario=sid, tier=tier.value):
                    result = analyze_query(prompt, tier)
                    self.assertTrue(
                        result.needs_decomposition,
                        f"{sid} did not enter decomposition on {tier.value}: "
                        f"is_compound={result.is_compound} "
                        f"sub_queries={result.sub_queries}",
                    )
                    self.assertEqual(
                        len(result.sub_queries), expected,
                        f"{sid} produced {len(result.sub_queries)} sub-queries "
                        f"on {tier.value}, the scenario asserts {expected}: "
                        f"{result.sub_queries}",
                    )

    def test_every_sub_query_is_non_empty_and_distinct(self):
        # A split that yields a blank or a duplicated clause would satisfy a
        # bare count assertion while still losing a request.
        for sid, prompt, _expected in DO_FOR_ME:
            for tier in TIERS:
                with self.subTest(scenario=sid, tier=tier.value):
                    subs = analyze_query(prompt, tier).sub_queries
                    self.assertTrue(all(s.strip() for s in subs), f"{sid}: {subs}")
                    self.assertEqual(len(set(subs)), len(subs), f"{sid}: {subs}")

    def test_the_six_that_were_missed_are_detected_as_compound(self):
        # detect_compound is the gate that failed; assert it directly so a
        # regression names the detector rather than only the end result.
        for sid, prompt, _expected in DO_FOR_ME:
            if sid not in FAILED_AT_BASE:
                continue
            with self.subTest(scenario=sid):
                self.assertTrue(
                    detect_compound(prompt),
                    f"{sid} matched no conjunction signal: {prompt!r}",
                )

    def test_split_and_count_agree_on_the_missed_verbs(self):
        # The defect was an asymmetry between the counting alternation and the
        # conjunction alternation. Pin that they now agree: a prompt whose
        # action count is >= 2 and whose clauses are substantive must split.
        for sid, prompt, expected in DO_FOR_ME:
            with self.subTest(scenario=sid):
                self.assertGreaterEqual(count_actions(prompt), 2, prompt)
                self.assertEqual(len(split_compound(prompt)), expected, prompt)


class DecompositionRestraintTest(unittest.TestCase):
    """Widening the conjunction alternation must not widen what decomposes."""

    def test_arithmetic_is_still_one_query(self):
        for tier in TIERS:
            with self.subTest(tier=tier.value):
                self.assertFalse(analyze_query("2 plus 2", tier).needs_decomposition)
                self.assertFalse(
                    analyze_query("what is 12 plus 30", tier).needs_decomposition)

    def test_contentless_fragment_is_still_one_query(self):
        # "look up and tell me what the hostname is" splits into a fragment
        # ("...look up") plus a clause; the substance guard rejects it.
        for tier in TIERS:
            with self.subTest(tier=tier.value):
                res = analyze_query(
                    "could you please look up and tell me what the hostname is",
                    tier)
                self.assertFalse(res.needs_decomposition)

    def test_a_single_action_with_an_and_object_is_not_compound(self):
        # "and" joining two OBJECTS, not two actions, must not split.
        for tier in TIERS:
            with self.subTest(tier=tier.value):
                self.assertFalse(
                    analyze_query("install firefox and thunderbird",
                                  tier).needs_decomposition)


class TierIndependenceTest(unittest.TestCase):
    """The decomposition decision is identical on 2B, 9B and 35B."""

    def test_same_split_on_every_tier(self):
        for sid, prompt, _expected in DO_FOR_ME:
            with self.subTest(scenario=sid):
                results = [analyze_query(prompt, t) for t in TIERS]
                first = results[0]
                for other, tier in zip(results[1:], TIERS[1:]):
                    self.assertEqual(
                        other.needs_decomposition, first.needs_decomposition,
                        f"{sid}: decomposition decision differs on {tier.value}")
                    self.assertEqual(
                        other.sub_queries, first.sub_queries,
                        f"{sid}: split differs on {tier.value}")

    def test_tier_threshold_is_diagnostic_only(self):
        # TIER_1's historical threshold is 1; a two-way split must still happen.
        res = analyze_query("check my disk usage and delete the temp files",
                            HardwareTierLevel.TIER_1)
        self.assertTrue(res.needs_decomposition)
        self.assertEqual(len(res.sub_queries), 2)


if __name__ == "__main__":
    unittest.main()
