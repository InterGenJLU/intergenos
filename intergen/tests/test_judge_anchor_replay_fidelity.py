# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A dimension the anchor replay cannot see must never count as judge drift.

THE DEFECT (2026-08-07). The anchor replay hands the judge the user's question,
the reply text and its source, and nothing about what the assistant dispatched —
not the tool calls that ran, and not the fact that none did. One banked reply's
recorded verdict was no_fabrication = fail and the replay returned pass, with
every other dimension reproducing exactly, so the overall difference was reported
as movement. A round that counts that as drift is reading a judge that was shown
less evidence as a judge that changed its mind.

WHICH DIMENSION, and why: no_fabrication asks whether the reply claims an action
that was never dispatched. That is a question ABOUT the dispatch record, not
about the reply, and the replay supplies no dispatch record — so a judge that
answers it is guessing, and a guess is not a measurement however it lands.
'correct' is NOT quarantined: it is answerable from the reply against the
rubric's own stated ground truth, and quarantining it would have thrown away the
dimension this set measures best. The remaining dimensions each ask about the
reply itself, which is exactly what the replay supplies.

The structural reason above stands alone deliberately. The empirical reason
originally recorded here — that no_fabrication's disagreements with the record
ran one way — was measured on a single host and did not survive a second one: on
another machine the same code with the same inputs returns 'fail' on the very
item that produced the clearest one-way disagreement. The two hosts differ on a
fixed set of three frozen replies with the direction reversing item by item, and
neither is the reference. That cross-host result argues the quarantine better
than the retracted sentence did: two judges given identical evidence answer this
dimension differently and confidently, which is what guessing looks like from
outside.

WHAT IS DELIBERATELY NOT CHANGED, and pinned below so it cannot drift back:
round-to-round comparison keeps counting every dimension. Both rounds are
replays at the same fidelity, so whatever the replay withholds it withholds from
both sides; a difference between them IS the judge changing its mind. What
compare_rounds gains instead is a refusal to compare two rounds taken at
DIFFERENT fidelity.

Pure data — no judge, no network, no daemon.
"""

from __future__ import annotations

import unittest

from intergen.tests import judge_anchor as ja
# Only names that exist at the parent commit are imported at module level; the
# new ones are reached through `ja.` INSIDE the cases. A module-level import of a
# name that does not exist yet kills collection for the whole file, and a red
# that is really a collection error proves nothing per-case.
from intergen.tests.judge_anchor import AnchorSetError, compare_rounds, summarize
from intergen.tests.quality_judge import RUBRIC_IDS

ALL_PASS = {d: "pass" for d in RUBRIC_IDS}


def _row(item_id, *, band="broken", recorded="pass", replay="pass",
         recorded_dims=None, replay_dims=None):
    """One round-record row, with the fidelity classification applied the same
    way regrade_anchor_set applies it."""
    recorded_dims = ALL_PASS if recorded_dims is None else recorded_dims
    replay_dims = ALL_PASS if replay_dims is None else replay_dims
    moved = recorded != replay
    return {
        "item_id": item_id, "band": band,
        "recorded_judge_overall": recorded, "regrade_overall": replay,
        "moved": moved,
        "direction": (0 if not moved else
                      ja.VERDICT_RANK[replay] - ja.VERDICT_RANK[recorded]),
        "regrade_dimensions": replay_dims, "regrade_evidence": {},
        "unparseable": False, "reasoning": "",
        "replay_fidelity": ja.classify_item_fidelity(
            recorded_dimensions=recorded_dims, replay_dimensions=replay_dims,
            moved=moved),
    }


def _round(rows, set_id="t7-judge-anchor-v1", fidelity=None):
    rec = {"set_id": set_id, "judge_model": "test", "items": rows}
    if fidelity is not False:
        rec["replay_fidelity"] = fidelity or ja.replay_fidelity_signature()
    return rec


class TheFidelityModelIsDeclaredAndFailsClosedTests(unittest.TestCase):

    def test_every_rubric_dimension_is_classified_one_way_or_the_other(self):
        self.assertEqual(
            ja.REPLAY_MEASURABLE_DIMENSIONS | ja.REPLAY_UNMEASURABLE_DIMENSIONS,
            frozenset(RUBRIC_IDS))
        self.assertEqual(
            ja.REPLAY_MEASURABLE_DIMENSIONS & ja.REPLAY_UNMEASURABLE_DIMENSIONS,
            frozenset())

    def test_the_dispatch_dimension_is_the_quarantined_one(self):
        self.assertIn("no_fabrication", ja.REPLAY_UNMEASURABLE_DIMENSIONS)
        self.assertIn("no_fabrication", ja.REPLAY_UNMEASURABLE_REASON)

    def test_the_dimension_the_set_measures_best_is_not_quarantined(self):
        # 'correct' is answerable from the reply against the rubric's own stated
        # ground truth, so the replay can measure it. Quarantining it would have
        # discarded real signal, and this is pinned deliberately.
        self.assertIn("correct", ja.REPLAY_MEASURABLE_DIMENSIONS)
        self.assertNotIn("correct", ja.REPLAY_UNMEASURABLE_DIMENSIONS)

    def test_a_new_rubric_dimension_is_quarantined_not_trusted(self):
        # THE FAIL-CLOSED DIRECTION, exercised through the MODULE's own
        # derivation rather than re-computed here. An earlier version of this
        # cell did the subtraction itself and passed even when the module was
        # mutated to the fail-open shape — it asserted its own arithmetic, not
        # the code's. The mutation check is what caught that.
        invented = "grounded_in_retrieval"
        self.assertNotIn(invented, ja.REPLAY_MEASURABLE_DIMENSIONS)
        grown = tuple(RUBRIC_IDS) + (invented,)
        self.assertIn(invented, ja.unmeasurable_dimensions(grown),
                      "a rubric dimension nobody classified must be treated as "
                      "unmeasurable, never as measurable by default")

    def test_the_declared_constant_is_that_function_applied_to_the_live_rubric(self):
        self.assertEqual(ja.REPLAY_UNMEASURABLE_DIMENSIONS,
                         ja.unmeasurable_dimensions(RUBRIC_IDS))


class MovementExplainedByMissingEvidenceIsNotDriftTests(unittest.TestCase):

    def test_the_measured_case_is_not_counted(self):
        # The banked item: only no_fabrication differs, and the overall moves.
        replay = dict(ALL_PASS, no_fabrication="pass")
        recorded = dict(ALL_PASS, no_fabrication="fail")
        f = ja.classify_item_fidelity(recorded_dimensions=recorded,
                                      replay_dimensions=replay, moved=True)
        self.assertFalse(f["drift_countable"])
        self.assertEqual(f["unmeasurable_dimensions_differing"], ["no_fabrication"])
        self.assertIn("no dispatch record", f["reason"])

    def test_movement_with_a_measurable_dimension_differing_still_counts(self):
        # An item whose 'honest' verdict also differs is NOT excused: only part
        # of its movement is the missing evidence, so it stays in the arithmetic.
        replay = dict(ALL_PASS, no_fabrication="flag", honest="flag")
        recorded = dict(ALL_PASS)
        f = ja.classify_item_fidelity(recorded_dimensions=recorded,
                                      replay_dimensions=replay, moved=True)
        self.assertTrue(f["drift_countable"])
        self.assertEqual(f["unmeasurable_dimensions_differing"], ["no_fabrication"])

    def test_an_item_with_no_recorded_dimensions_is_not_counted(self):
        # Fail closed: nothing can be attributed, so nothing may be claimed.
        f = ja.classify_item_fidelity(recorded_dimensions={},
                                      replay_dimensions=ALL_PASS, moved=True)
        self.assertFalse(f["drift_countable"])
        self.assertIn("no recorded per-dimension verdicts", f["reason"])

    def test_an_unmoved_item_stays_countable_but_the_difference_is_visible(self):
        replay = dict(ALL_PASS, no_fabrication="flag")
        f = ja.classify_item_fidelity(recorded_dimensions=dict(ALL_PASS),
                                      replay_dimensions=replay, moved=False)
        self.assertTrue(f["drift_countable"])
        self.assertEqual(f["unmeasurable_dimensions_differing"], ["no_fabrication"])


class TheSummaryReportsBothArithmeticsTests(unittest.TestCase):

    def _mixed_round(self):
        return _round([
            _row("anchor-0001"),
            _row("anchor-0016", recorded="fail", replay="flag",
                 recorded_dims=dict(ALL_PASS, no_fabrication="fail"),
                 replay_dims=dict(ALL_PASS, no_fabrication="pass")),
            _row("anchor-0029", recorded="pass", replay="flag",
                 recorded_dims=dict(ALL_PASS),
                 replay_dims=dict(ALL_PASS, no_fabrication="flag", honest="flag")),
            _row("anchor-0011", recorded="flag", replay="fail", recorded_dims={}),
        ])

    def test_the_raw_count_is_unchanged_so_old_rounds_stay_continuous(self):
        s = summarize(self._mixed_round())
        self.assertEqual(s["moved"], 3)
        self.assertEqual(s["items_total"], 4)

    def test_the_drift_count_excludes_what_the_replay_cannot_see(self):
        s = summarize(self._mixed_round())
        self.assertEqual(s["items_drift_countable"], 2)
        self.assertEqual(s["moved_measurable"], 1)          # only anchor-0029
        self.assertEqual(s["agreement_rate_measurable"], 0.5)

    def test_the_excluded_items_are_named_with_a_reason_not_dropped(self):
        s = summarize(self._mixed_round())
        excluded = {e["item_id"]: e["reason"] for e in s["not_drift_countable"]}
        self.assertEqual(set(excluded), {"anchor-0016", "anchor-0011"})
        for reason in excluded.values():
            self.assertTrue(reason.strip(), "an exclusion with no stated reason")

    def test_the_summary_names_the_unmeasurable_dimensions(self):
        s = summarize(self._mixed_round())
        self.assertEqual(s["unmeasurable_dimensions"], ["no_fabrication"])

    def test_a_round_written_before_the_fidelity_model_still_summarizes(self):
        old = _round([{**_row("anchor-0001", recorded="fail", replay="flag"),
                       "replay_fidelity": None}])
        old["items"][0].pop("replay_fidelity")
        s = summarize(old)
        self.assertEqual(s["moved"], 1)
        self.assertEqual(s["items_drift_countable"], 1)


class RoundToRoundComparisonKeepsCountingEverythingTests(unittest.TestCase):
    """The deliberate non-change, pinned so it cannot quietly drift."""

    def test_a_change_in_an_unmeasurable_dimension_still_counts_between_rounds(self):
        a = _round([_row("anchor-0016", recorded="fail", replay="flag",
                         recorded_dims=dict(ALL_PASS, no_fabrication="fail"),
                         replay_dims=dict(ALL_PASS, no_fabrication="pass"))])
        b = _round([_row("anchor-0016", recorded="fail", replay="fail",
                         recorded_dims=dict(ALL_PASS, no_fabrication="fail"),
                         replay_dims=dict(ALL_PASS, no_fabrication="fail"))])
        diff = compare_rounds(a, b)
        self.assertEqual(diff["changed"], 1,
                         "round-to-round movement is judge drift at identical "
                         "fidelity and must not be excused by the fidelity model")
        self.assertEqual(diff["stability_rate"], 0.0)

    def test_the_comparison_states_the_fidelity_it_was_taken_at(self):
        a = _round([_row("anchor-0001")])
        b = _round([_row("anchor-0001")])
        diff = compare_rounds(a, b)
        self.assertEqual(diff["replay_fidelity"], ja.replay_fidelity_signature())
        self.assertIn("identical fidelity", diff["fidelity_note"])


class ComparingRoundsAtDifferentFidelityIsRefusedTests(unittest.TestCase):

    def test_two_rounds_with_different_inputs_are_refused(self):
        a = _round([_row("anchor-0001")])
        b = _round([_row("anchor-0001")],
                   fidelity=dict(ja.replay_fidelity_signature(),
                                 input_fields=["user_input", "response_text",
                                               "source", "tool_calls"]))
        with self.assertRaises(AnchorSetError) as cm:
            compare_rounds(a, b)
        self.assertIn("DIFFERENT replay fidelity", str(cm.exception))

    def test_a_round_with_no_signature_is_refused_by_name(self):
        a = _round([_row("anchor-0001")])
        b = _round([_row("anchor-0001")], fidelity=False)
        with self.assertRaises(AnchorSetError) as cm:
            compare_rounds(a, b)
        self.assertIn("no replay_fidelity signature", str(cm.exception))
        self.assertIn("round B", str(cm.exception))

    def test_different_sets_are_still_refused_first(self):
        a = _round([_row("anchor-0001")])
        b = _round([_row("anchor-0001")], set_id="t7-judge-anchor-v2")
        with self.assertRaises(AnchorSetError) as cm:
            compare_rounds(a, b)
        self.assertIn("different anchor sets", str(cm.exception))


class TheSignatureDescribesWhatIsWithheldTests(unittest.TestCase):

    def test_the_signature_names_the_withheld_evidence(self):
        sig = ja.replay_fidelity_signature()
        joined = " ".join(sig["withheld_evidence"])
        self.assertIn("assembled_prompt", joined)
        self.assertIn("tool_calls", joined)
        self.assertIn("tool results", joined)

    def test_the_signature_lists_the_fields_the_replay_actually_feeds(self):
        sig = ja.replay_fidelity_signature()
        self.assertEqual(sig["input_fields"],
                         ["user_input", "response_text", "source"])

    def test_the_replay_does_not_feed_tool_calls_and_says_why(self):
        # If tool_calls are ever fed in, this cell fails and the signature must
        # change with it — which is what makes old rounds refuse to compare.
        item = {"item_id": "x", "user_input": "u", "response_text": "r",
                "source": "keyword",
                "tool_calls": [{"name": "manage_packages",
                                "arguments": {"action": "search"}}]}
        inputs = ja._inputs_for_item(item)
        self.assertEqual(inputs.assembled_prompt, "")
        self.assertEqual(inputs.antecedent, "")
        self.assertNotIn("manage_packages", inputs.user_input + inputs.delivered)


if __name__ == "__main__":
    unittest.main()
