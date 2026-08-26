# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M8-2, second pass: a successful dispatch's value REACHES the delivered answer.

Ground truth for these fixtures is the whole-battery scenario run on dev
601b2f790 (2026-08-26, 2B tier), which emitted exactly two M8-2 warnings across
674 scenarios. Reading the two replies the run recorded changed what the defect is:

WRT-do-for-me-04, turn 0f910e800ecb975c, dispatch manage_services. The reply
carries the result AND then denies having it:
    "**1.** 441 active services are running.
     **2.** I don't have current data on running services ..."
The value DID reach the answer; the same answer contradicts it. Reported as
"deflection_despite_result" — "its result did not reach the delivered answer" —
which is not what happened.

WRT-do-for-me-06, turn 8cd4535ca76be728, dispatch web_search. The reply reports
all five search results and then adds "You can use the `crontab` command ...".
The teaching sentence matched the explain-instead-of-result marker even though
the result is reported in full, immediately above it. That is a false positive:
nothing was dropped.

So the detector reads a text SHAPE anywhere in the answer as proof the result was
dropped, without ever asking whether the result is ALSO in the answer. These
fixtures pin the corrected behaviour:

  * a marker beside an EVIDENCED result is not a drop — a deflection there is
    named for what it is (contradicted_despite_result), and a teaching addendum
    after a reported result is not a defect at all;
  * a result that genuinely did not reach the answer is still flagged, and is now
    CARRIED INTO the delivered answer instead of only being logged.

TIERS. Nothing in this path branches on the model tier: safety's detector, the
D-Bus delivery site and the web streamed site contain no tier, model-size or
config gate (measured by reading at branch time). Every case therefore runs under
each tier's real configuration entry (hardware.TIER_MODELS: 2B, 9B, 35B) and must
behave identically; a divergence between tiers is itself a failure.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from intergen import safety
from intergen.hardware import TIER_MODELS
from intergen.interfaces.types import AnswerLinkage, RouteResult, ToolResult


TIERS = [(level.name, spec["name"]) for level, spec in sorted(
    TIER_MODELS.items(), key=lambda kv: kv[0].value)]


class _Res:
    """ToolResult stand-in — the tool is stubbed, nothing executes."""

    def __init__(self, name="manage_services", content="", model_summary="",
                 call_id="c1", executed=True, success=True, blocked=False):
        self.name = name
        self.content = content
        self.model_summary = model_summary
        self.call_id = call_id
        self.executed = executed
        self.success = success
        self.blocked = blocked


# The battery's own two replies, verbatim from results.json.
REPLY_04 = (
    "I see 3 things you'd like done. Let me take them one at a time so I get "
    "each one right.\n\n\n\n**1.** 441 active services are running.\n\n**2.** I "
    "don't have current data on running services or specific service statuses. "
    "However, I can help you restart a stopped service")
RESULT_04 = ("441 active services running; cups.service loaded active running; "
             "bluetooth.service loaded inactive dead")

REPLY_06 = (
    "The tool returned the following search results for setting up and creating "
    "a cron job:\n\n1. **Scheduling Cron Jobs with Crontab**: A comprehensive "
    "guide to using `crontab` for scheduling jobs.\n2. **How to Set Up a Cron "
    "Job in Linux**: Instructions from phoenixNAP on setting up cron jobs in "
    "Linux.\n3. **Automate Tasks with Cron Jobs in Linux**: A guide from "
    "GeeksforGeeks on automating tasks with cron jobs.\n4. **Cron job**: An "
    "overview of cron and setup instructions from Hostinger.\n5. **Cronitor**: A "
    "guide on cron jobs, including syntax and management.\n\nYou can use the "
    "`crontab` command to create and manage cron jobs.")
RESULT_06 = ("Scheduling Cron Jobs with Crontab | How to Set Up a Cron Job in "
             "Linux - phoenixNAP | Automate Tasks with Cron Jobs in Linux - "
             "GeeksforGeeks | Cron job - Hostinger | Cronitor cron guide")


class DetectorPrecisionTests(unittest.TestCase):
    """A marker beside a result that IS in the answer is not a dropped result."""

    def test_a_deflection_beside_an_evidenced_result_is_a_contradiction(self):
        """WRT-do-for-me-04: '441 active services are running' IS the result.

        Calling that 'the result did not reach the delivered answer' is false.
        It reached it; the next sentence denies it. Distinct, accurate name.
        """
        for tier_name, model in TIERS:
            with self.subTest(tier=tier_name, model=model):
                probs = safety.find_unconsumed_dispatches(
                    REPLY_04, [_Res(content=RESULT_04)])
                self.assertEqual([r for _, r in probs],
                                 ["contradicted_despite_result"])

    def test_a_teaching_addendum_after_the_reported_result_is_clean(self):
        """WRT-do-for-me-06: all five results are reported, then one 'you can
        use' sentence. Nothing was dropped, so nothing is flagged."""
        for tier_name, model in TIERS:
            with self.subTest(tier=tier_name, model=model):
                self.assertEqual(
                    safety.find_unconsumed_dispatches(
                        REPLY_06, [_Res(name="web_search", content=RESULT_06)]),
                    [])

    def test_a_deflection_with_the_result_absent_is_still_a_drop(self):
        """True-positive control: the shape the invariant exists for."""
        for tier_name, model in TIERS:
            with self.subTest(tier=tier_name, model=model):
                probs = safety.find_unconsumed_dispatches(
                    "I don't have current data on your services.",
                    [_Res(content=RESULT_04)])
                self.assertEqual([r for _, r in probs],
                                 ["deflection_despite_result"])

    def test_a_teaching_answer_with_the_result_absent_is_still_a_drop(self):
        """True-positive control for the M7 leg-4 shape."""
        for tier_name, model in TIERS:
            with self.subTest(tier=tier_name, model=model):
                probs = safety.find_unconsumed_dispatches(
                    "Here's how you'd check: run `systemctl list-units`.",
                    [_Res(content=RESULT_04)])
                self.assertEqual([r for _, r in probs],
                                 ["explain_instead_of_result"])


class RepairHelperTests(unittest.TestCase):
    """The result is carried into the answer, stated as the tool produced it."""

    def test_the_repair_states_the_result_verbatim(self):
        for tier_name, model in TIERS:
            with self.subTest(tier=tier_name, model=model):
                fixed = safety.carry_result_into_answer(
                    "I don't have current data on your services.",
                    _Res(content=RESULT_04), "deflection_despite_result")
                self.assertIsNotNone(fixed)
                self.assertIn(RESULT_04, fixed)

    def test_the_repair_drops_the_denial_it_replaces(self):
        """Keeping the denial beside the data would manufacture the very
        contradiction WRT-do-for-me-04 was flagged for."""
        fixed = safety.carry_result_into_answer(
            "I don't have current data on your services.",
            _Res(content=RESULT_04), "deflection_despite_result")
        self.assertNotIn("I don't have current data", fixed)

    def test_an_empty_delivery_is_repaired_with_the_result(self):
        fixed = safety.carry_result_into_answer(
            "", _Res(content=RESULT_04), "empty_delivery")
        self.assertIn(RESULT_04, fixed)

    def test_a_teaching_answer_keeps_its_instructions_below_the_result(self):
        """The teaching sentences are useful and are not a false statement about
        what the system knows, so only the missing result is added — above them,
        because the answer to what was asked comes first."""
        text = "Here's how you'd check: run `systemctl list-units`."
        fixed = safety.carry_result_into_answer(
            text, _Res(content=RESULT_04), "explain_instead_of_result")
        self.assertTrue(fixed.startswith(RESULT_04))
        self.assertIn(text, fixed)

    def test_a_result_with_no_content_is_never_a_repair(self):
        """Nothing to state — the answer is left as composed rather than blanked."""
        self.assertIsNone(safety.carry_result_into_answer(
            "I don't have current data.", _Res(content="", model_summary=""),
            "deflection_despite_result"))

    def test_a_substituted_result_is_never_rewritten(self):
        """The linkage signal cannot tell a true substitution from a summarizer
        answering off an authoritative live source, so this class stays
        observability-only — rewriting it would overwrite correct answers."""
        self.assertIsNone(safety.carry_result_into_answer(
            "Disk usage is available.", _Res(content=RESULT_04), "substituted"))

    def test_a_contradiction_is_never_rewritten(self):
        """The value is already in the answer; there is nothing to carry in."""
        self.assertIsNone(safety.carry_result_into_answer(
            REPLY_04, _Res(content=RESULT_04), "contradicted_despite_result"))


class DbusDeliveryCarriesTheResultTests(unittest.TestCase):
    """The real D-Bus delivery site, invoked — router and tool stubbed."""

    def _ask(self, result):
        from intergen.dbus_daemon import InterGenDaemon
        daemon = InterGenDaemon()
        daemon._router = mock.Mock()
        daemon._router.route.return_value = result
        with mock.patch("intergen.review_modal.make_review_callback",
                        return_value=None):
            return json.loads(daemon.ask("what services are running?"))

    def _dropped(self):
        tr = ToolResult(call_id="c1", name="manage_services",
                        content=RESULT_04)
        return RouteResult(
            text="I don't have current data on your services.",
            source="keyword", handled=True, tool_results=[tr], used_llm=True,
            answer_linkage=AnswerLinkage(kind="dispatch", tool="manage_services",
                                         call_id="c1", renderer="llm_synth"))

    def test_the_delivered_answer_carries_the_result(self):
        for tier_name, model in TIERS:
            with self.subTest(tier=tier_name, model=model):
                payload = self._ask(self._dropped())
                self.assertIn(RESULT_04, payload["response"])

    def test_the_delivered_answer_no_longer_denies_the_data(self):
        payload = self._ask(self._dropped())
        self.assertNotIn("I don't have current data", payload["response"])

    def test_an_answer_that_already_carries_the_result_is_untouched(self):
        tr = ToolResult(call_id="c1", name="manage_services", content=RESULT_04)
        res = RouteResult(text=REPLY_04, source="keyword", handled=True,
                          tool_results=[tr], used_llm=True,
                          answer_linkage=AnswerLinkage(
                              kind="dispatch", tool="manage_services",
                              call_id="c1", renderer="llm_synth"))
        self.assertEqual(self._ask(res)["response"], REPLY_04)


if __name__ == "__main__":
    unittest.main()
