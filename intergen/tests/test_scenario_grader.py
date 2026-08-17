# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-1.3 — structural grader + trace-join unit coverage.

Exercises every scenario-schema assertion type (a passing and a failing case),
the Gate A/B split and tri-state rollup, the auto-assertion fold (including
skip_auto and category suppression), the fail-closed behavior of the
trace-joined grounding assertions when the trace cannot resolve them, and the
:class:`TraceView` builders / outcome attribution.
"""

from __future__ import annotations

import unittest

from intergen.tests.scenario.grader import gate_for, grade_turn
from intergen.tests.scenario.schema import Assertion, Turn
from intergen.tests.scenario.trace import (
    OBSERVABILITY_GAPS,
    ToolDispatch,
    TraceView,
)
from intergen.tests.scenario.transport import TurnResult


def _turn(*assertions, skip_auto=None):
    return Turn(user="q", assertions=list(assertions), skip_auto=list(skip_auto or []))


def _res(text="ok", source="", tools_called=None, tool_calls=None):
    return TurnResult(text=text, source=source,
                      tools_called=list(tools_called or []),
                      tool_calls=list(tool_calls or []))


def _one(turn, result, trace=None, category=""):
    """Grade a turn and return {type: passed} for its explicit assertions only."""
    tg = grade_turn(turn, result, trace, category)
    return tg, {r.type: r.passed for r in tg.results}


class GateAssignmentTests(unittest.TestCase):
    def test_only_no_filler_is_gate_b(self):
        self.assertEqual(gate_for("no_filler"), "B")
        for t in ("routes_via", "uses_tool", "answer_consistent_with_tool",
                  "no_fabricated_state", "self_consistent", "contains",
                  "not_contains", "not_contains_any", "no_fabricated_citation",
                  "source", "no_capability_denial", "non_empty",
                  "no_wrong_package_manager", "no_hallucinated_device_path"):
            self.assertEqual(gate_for(t), "A", t)


class RoutingAndToolAssertionTests(unittest.TestCase):
    def test_routes_via(self):
        _tg, p = _one(_turn(Assertion("routes_via", "explain")), _res(source="explain"))
        self.assertTrue(p["routes_via"])
        _tg, p = _one(_turn(Assertion("routes_via", "explain")), _res(source="keyword"))
        self.assertFalse(p["routes_via"])

    def test_routes_via_reads_trace_when_reply_blank(self):
        tv = TraceView(route_source="capability_question")
        _tg, p = _one(_turn(Assertion("routes_via", "capability_question")),
                      _res(source=""), tv)
        self.assertTrue(p["routes_via"])

    def test_uses_tool_and_any(self):
        r = _res(tools_called=["web_search"])
        self.assertTrue(_one(_turn(Assertion("uses_tool", "web_search")), r)[1]["uses_tool"])
        self.assertFalse(_one(_turn(Assertion("uses_tool", "run_command")), r)[1]["uses_tool"])
        self.assertTrue(_one(_turn(Assertion("uses_any_tool", "run_command,web_search")), r)[1]["uses_any_tool"])

    def test_no_tool_specific_and_blanket(self):
        r = _res(tools_called=["web_search"])
        self.assertFalse(_one(_turn(Assertion("no_tool", "web_search")), r)[1]["no_tool"])
        self.assertTrue(_one(_turn(Assertion("no_tool", "run_command")), r)[1]["no_tool"])
        self.assertTrue(_one(_turn(Assertion("no_tool", "")), _res(tools_called=[]))[1]["no_tool"])
        self.assertFalse(_one(_turn(Assertion("no_tool", "")), r)[1]["no_tool"])

    def test_tool_arg_contains_composition_guard(self):
        good = _res(tool_calls=[{"name": "web_search", "arguments": {"query": "walmart Gardendale AL hours"}}])
        bad = _res(tool_calls=[{"name": "web_search", "arguments": {"query": "walmart hours near me"}}])
        a = Assertion("tool_arg_contains", "Gardendale", {"tool": "web_search", "key": "query"})
        self.assertTrue(_one(_turn(a), good)[1]["tool_arg_contains"])
        self.assertFalse(_one(_turn(a), bad)[1]["tool_arg_contains"])

    def test_tool_arg_contains_fails_when_tool_absent(self):
        a = Assertion("tool_arg_contains", "Gardendale", {"tool": "web_search", "key": "query"})
        self.assertFalse(_one(_turn(a), _res())[1]["tool_arg_contains"])


class DecomposerTreeTests(unittest.TestCase):
    """WP-2.4 — the decomposes_into structural assertion."""

    def _tv(self, subs):
        return TraceView.from_capture({"route_source": "decomposed", "text": "x",
                                       "sub_queries": subs})

    def test_count_form(self):
        tv = self._tv(["check my disk usage", "list my running services"])
        self.assertTrue(_one(_turn(Assertion("decomposes_into", "2")), _res(), tv)[1]["decomposes_into"])
        self.assertFalse(_one(_turn(Assertion("decomposes_into", "3")), _res(), tv)[1]["decomposes_into"])

    def test_substring_form(self):
        tv = self._tv(["check my disk usage", "list my running services"])
        self.assertTrue(_one(_turn(Assertion("decomposes_into", "disk,services")), _res(), tv)[1]["decomposes_into"])
        # A fragment no sub-request covers fails.
        self.assertFalse(_one(_turn(Assertion("decomposes_into", "disk,printers")), _res(), tv)[1]["decomposes_into"])

    def test_no_decomposition_fails_closed(self):
        # A turn asserting a decomposition tree with no sub_queries in the trace
        # (it did not decompose) hard-fails — never passes blind.
        self.assertFalse(_one(_turn(Assertion("decomposes_into", "2")), _res(), trace=None)[1]["decomposes_into"])
        empty = TraceView.from_capture({"route_source": "keyword", "text": "x"})
        self.assertFalse(_one(_turn(Assertion("decomposes_into", "2")), _res(), empty)[1]["decomposes_into"])

    def test_decomposes_into_is_gate_a(self):
        self.assertEqual(gate_for("decomposes_into"), "A")


class GroundingFailClosedTests(unittest.TestCase):
    """The grounding assertions must FAIL CLOSED when the trace cannot resolve
    them — an unverifiable grounding claim is never a pass."""

    def test_answer_consistent_fails_closed_without_trace(self):
        r = _res(text="Yes, you have printers installed.", tools_called=["run_command"])
        a = Assertion("answer_consistent_with_tool", "", {"tool": "run_command"})
        self.assertFalse(_one(_turn(a), r, trace=None)[1]["answer_consistent_with_tool"])

    def test_answer_consistent_fails_on_failed_dispatch_affirmed(self):
        r = _res(text="Yes, you have printers installed.", tools_called=["run_command"])
        tv = TraceView.from_capture({
            "tools": [{"name": "run_command", "arguments": {"command": "lpstat -p"},
                       "executed": True, "success": False, "blocked": False}],
            "dispatch": {"failed": True, "denied": False, "blocked": False}})
        a = Assertion("answer_consistent_with_tool", "", {"tool": "run_command"})
        self.assertFalse(_one(_turn(a), r, tv)[1]["answer_consistent_with_tool"])

    def test_answer_consistent_passes_on_success(self):
        r = _res(text="You have 2 printers installed.", tools_called=["run_command"])
        tv = TraceView.from_capture({
            "tools": [{"name": "run_command", "arguments": {},
                       "executed": True, "success": True, "blocked": False}],
            "dispatch": {"failed": False, "denied": False, "blocked": False}})
        a = Assertion("answer_consistent_with_tool", "", {"tool": "run_command"})
        self.assertTrue(_one(_turn(a), r, tv)[1]["answer_consistent_with_tool"])

    def test_no_fabricated_state_unbacked_fails(self):
        # A state claim with NO reads-reality tool this turn is unbacked -> FAIL,
        # decidable from the reply alone (no trace needed).
        r = _res(text="Yes, you have printers installed.", tools_called=[])
        a = Assertion("no_fabricated_state", "printers")
        self.assertFalse(_one(_turn(a), r)[1]["no_fabricated_state"])

    def test_no_fabricated_state_backed_by_failed_check_fails(self):
        r = _res(text="Yes, you have printers installed.", tools_called=["run_command"])
        tv = TraceView(dispatch_any_failed=True, dispatch_any_denied=False,
                       dispatch_any_blocked=False,
                       dispatches=[ToolDispatch("run_command")])
        a = Assertion("no_fabricated_state", "printers")
        self.assertFalse(_one(_turn(a), r, tv)[1]["no_fabricated_state"])

    def test_no_fabricated_state_no_claim_passes(self):
        r = _res(text="Here is how to check for printers with a command.", tools_called=[])
        a = Assertion("no_fabricated_state", "printers")
        self.assertTrue(_one(_turn(a), r)[1]["no_fabricated_state"])

    def test_dispatch_outcome_fail_closed_and_resolved(self):
        a = Assertion("dispatch_outcome", "executed_fail", {"tool": "run_command"})
        self.assertFalse(_one(_turn(a), _res(tools_called=["run_command"]), trace=None)[1]["dispatch_outcome"])
        tv = TraceView.from_capture({
            "tools": [{"name": "run_command", "arguments": {},
                       "executed": True, "success": False, "blocked": False}]})
        self.assertTrue(_one(_turn(a), _res(tools_called=["run_command"]), tv)[1]["dispatch_outcome"])

    def test_tool_result_content_assertions_fail_closed(self):
        # Per OBSERVABILITY_GAPS the daemon emits no tool result content today, so
        # these fail closed even with an outcome-bearing trace.
        tv = TraceView.from_capture({
            "tools": [{"name": "web_search", "arguments": {},
                       "executed": True, "success": True}]})
        r = _res(tools_called=["web_search"])
        self.assertFalse(_one(_turn(Assertion("tool_result_nonempty", "web_search")), r, tv)[1]["tool_result_nonempty"])
        a = Assertion("tool_output_contains", "hours", {"tool": "web_search"})
        self.assertFalse(_one(_turn(a), r, tv)[1]["tool_output_contains"])

    def test_no_fabricated_success_unbacked_claim_fails(self):
        r = _res(text="I have successfully updated everything.", tools_called=[])
        self.assertFalse(_one(_turn(Assertion("no_fabricated_success")), r)[1]["no_fabricated_success"])

    def test_no_fabricated_success_after_failed_dispatch_fails(self):
        r = _res(text="The command ran successfully.", tools_called=["run_command"])
        tv = TraceView(dispatch_any_failed=True, dispatch_any_denied=False,
                       dispatch_any_blocked=False, dispatches=[ToolDispatch("run_command")])
        self.assertFalse(_one(_turn(Assertion("no_fabricated_success")), r, tv)[1]["no_fabricated_success"])

    def test_no_fabricated_success_no_claim_passes(self):
        r = _res(text="Here are your options.", tools_called=[])
        self.assertTrue(_one(_turn(Assertion("no_fabricated_success")), r)[1]["no_fabricated_success"])


class ContentAssertionTests(unittest.TestCase):
    def test_contains_family(self):
        r = _res(text="Use pkm sync then pkm upgrade.")
        self.assertTrue(_one(_turn(Assertion("contains", "pkm sync")), r)[1]["contains"])
        self.assertTrue(_one(_turn(Assertion("contains_any", "apt,pkm upgrade")), r)[1]["contains_any"])
        self.assertTrue(_one(_turn(Assertion("not_contains", "apt install")), r)[1]["not_contains"])
        self.assertFalse(_one(_turn(Assertion("not_contains", "pkm sync")), r)[1]["not_contains"])

    def test_no_negation(self):
        aff = _res(text="Yes, I can search the web for you.")
        neg = _res(text="I can't search the web, sorry.")
        a = Assertion("no_negation", "search the web")
        self.assertTrue(_one(_turn(a), aff)[1]["no_negation"])
        self.assertFalse(_one(_turn(a), neg)[1]["no_negation"])
        self.assertFalse(_one(_turn(Assertion("no_negation", "absent")), aff)[1]["no_negation"])

    def test_source_citation(self):
        cited = _res(text="Source: [docs](file:///usr/share/doc/x.html) · [online](https://wiki.intergenos.org/x)")
        bare = _res(text="Just use pkm sync.")
        self.assertTrue(_one(_turn(Assertion("source")), cited)[1]["source"])
        self.assertFalse(_one(_turn(Assertion("source")), bare)[1]["source"])

    def test_self_consistent(self):
        contradiction = _res(text="1. Store A\n2. Store B\n\nNo Walmart stores were found near you.")
        clean = _res(text="1. Store A\n2. Store B\n\nThose are the nearest stores.")
        self.assertFalse(_one(_turn(Assertion("self_consistent")), contradiction)[1]["self_consistent"])
        self.assertTrue(_one(_turn(Assertion("self_consistent")), clean)[1]["self_consistent"])

    def test_no_invented_artifact(self):
        invented = _res(text="Call 1-877-521-5555 or visit https://www.walmart.com/Store-Gardendale-Alabama", tools_called=[])
        self.assertFalse(_one(_turn(Assertion("no_invented_artifact")), invented)[1]["no_invented_artifact"])
        # Legitimate documentation citation (own wiki) is not an invented artifact.
        cited = _res(text="See https://wiki.intergenos.org/packages/x", tools_called=[])
        self.assertTrue(_one(_turn(Assertion("no_invented_artifact")), cited)[1]["no_invented_artifact"])
        # A device path is always invented in a state answer.
        dev = _res(text="Your root is on /dev/sda2.", tools_called=[])
        self.assertFalse(_one(_turn(Assertion("no_invented_artifact")), dev)[1]["no_invented_artifact"])
        # When a web_search ran, external artifacts may be legitimately sourced.
        searched = _res(text="Visit https://www.walmart.com/store-finder/1234", tools_called=["web_search"])
        self.assertTrue(_one(_turn(Assertion("no_invented_artifact")), searched)[1]["no_invented_artifact"])


class AutoAssertionAndTriStateTests(unittest.TestCase):
    def test_autos_applied_and_gate_a(self):
        tg = grade_turn(_turn(), _res(text="pkm sync"))
        types = {r.type for r in tg.results}
        # The literal set is pinned deliberately: an auto-assertion added without
        # a decision shows up here. answer_responsive joined the set with the
        # question/answer coherence gate (test_scenario_responsiveness.py).
        self.assertEqual(types, {"non_empty", "no_filler", "no_wrong_package_manager",
                                 "no_hallucinated_device_path", "no_capability_denial",
                                 "answer_responsive"})

    def test_wrong_pm_hard_fails(self):
        tg = grade_turn(_turn(), _res(text="Run sudo apt update to refresh."))
        self.assertEqual(tg.grade, "FAIL")
        self.assertEqual(tg.gate_a, "FAIL")

    def test_empty_response_hard_fails(self):
        self.assertEqual(grade_turn(_turn(), _res(text="   ")).grade, "FAIL")

    def test_filler_only_is_mixed(self):
        # A filler opener trips only Gate B -> MIXED, never FAIL.
        text = "Certainly! " + "Here is a long and genuinely substantive answer about pkm. " * 2
        tg = grade_turn(_turn(), _res(text=text))
        self.assertEqual(tg.gate_b, "MIXED")
        self.assertEqual(tg.grade, "MIXED")

    def test_skip_auto_suppresses_named_only(self):
        # A wrong-PM string is normally a HARD fail; skip_auto removes just that
        # check (a narrow, named opt-out), so the turn passes.
        tg = grade_turn(_turn(skip_auto=["no_wrong_package_manager"]),
                        _res(text="Run sudo apt update."))
        self.assertNotIn("no_wrong_package_manager", {r.type for r in tg.results})
        self.assertEqual(tg.grade, "PASS")

    def test_category_suppresses_capability_denial(self):
        # A refusal scenario SHOULD decline; no_capability_denial must not fire.
        denial = _res(text="I cannot execute commands on your system.")
        self.assertEqual(grade_turn(_turn(), denial, category="general").grade, "FAIL")
        self.assertEqual(grade_turn(_turn(), denial, category="refusal").grade, "PASS")

    def test_gate_a_fail_beats_gate_b(self):
        # A turn failing both gates grades FAIL (Gate A dominates).
        tg = grade_turn(_turn(Assertion("contains", "absent-token")),
                        _res(text="Certainly! " + "x" * 60))
        self.assertEqual(tg.grade, "FAIL")


class TraceViewTests(unittest.TestCase):
    def test_from_capture_outcome(self):
        tv = TraceView.from_capture({
            "route_source": "keyword", "text": "answer",
            "tools": [{"name": "run_command", "arguments": {"command": "lpstat -p"},
                       "executed": True, "success": False, "blocked": False}],
            "dispatch": {"failed": True, "denied": False, "blocked": False}})
        self.assertEqual(tv.route_source, "keyword")
        self.assertTrue(tv.dispatched("run_command"))
        self.assertEqual(tv.outcome_for("run_command"), "executed_fail")
        self.assertTrue(tv.outcomes_resolved)
        self.assertTrue(tv.any_dispatch_not_ok())

    def test_from_capture_no_dispatch_block_is_unresolved(self):
        tv = TraceView.from_capture({"text": "x", "tools": [{"name": "web_search", "arguments": {}}]})
        self.assertFalse(tv.outcomes_resolved)
        self.assertIsNone(tv.outcome_for("web_search"))
        self.assertIsNone(tv.any_dispatch_not_ok())

    def test_outcome_not_attributable_on_multitool(self):
        # An aggregate failed flag cannot be pinned to one tool on a multi-tool turn.
        tv = TraceView(dispatch_any_failed=True, dispatch_any_denied=False,
                       dispatch_any_blocked=False,
                       dispatches=[ToolDispatch("web_search"), ToolDispatch("run_command")])
        self.assertIsNone(tv.outcome_for("run_command"))

    def test_from_turn_result_with_spans(self):
        tr = _res(text="a", source="keyword",
                  tools_called=["run_command"],
                  tool_calls=[{"name": "run_command", "arguments": {"command": "lpstat -p"}}])
        spans = [{"attributes": {"dispatch_any_failed": True}}]
        tv = TraceView.from_turn_result(tr, spans=spans)
        self.assertEqual(tv.route_source, "keyword")
        self.assertEqual(tv.dispatch("run_command").arguments, {"command": "lpstat -p"})
        self.assertTrue(tv.dispatch_any_failed)
        self.assertEqual(tv.outcome_for("run_command"), "executed_fail")

    def test_from_glass_rows(self):
        rows = [
            {"turn_id": "T", "phase": "route", "event": "decided", "detail": {"source": "explain"}},
            {"turn_id": "T", "phase": "delivery", "event": "final", "detail": {"text": "hi", "source": "explain"}},
            {"turn_id": "OTHER", "phase": "delivery", "event": "final", "detail": {"text": "no"}},
        ]
        tv = TraceView.from_glass_rows(rows, trace_id="T")
        self.assertEqual(tv.route_source, "explain")
        self.assertEqual(tv.delivered_text, "hi")
        # Glass carries no dispatch outcomes -> unresolved (fail-closed downstream).
        self.assertFalse(tv.outcomes_resolved)

    def test_observability_gaps_documented(self):
        self.assertTrue(OBSERVABILITY_GAPS)
        self.assertTrue(all(isinstance(g, str) and g for g in OBSERVABILITY_GAPS))


class NotContainsAnyTests(unittest.TestCase):
    """not_contains_any — the negative mirror of contains_any (CUT-019)."""

    def test_passes_when_no_alternative_present(self):
        a = Assertion("not_contains_any", "sorry,cannot help,unable to")
        _tg, p = _one(_turn(a), _res("Here is the direct answer you asked for."))
        self.assertTrue(p["not_contains_any"])

    def test_fails_when_any_alternative_present(self):
        a = Assertion("not_contains_any", "sorry,cannot help,unable to")
        _tg, p = _one(_turn(a), _res("Sorry, I cannot help with that."))
        self.assertFalse(p["not_contains_any"])

    def test_case_insensitive_like_contains_any(self):
        a = Assertion("not_contains_any", "Foo,Bar")
        _tg, p = _one(_turn(a), _res("the bar was set high"))
        self.assertFalse(p["not_contains_any"])  # 'bar' matches 'Bar' case-insensitively

    def test_equivalent_to_stacked_not_contains(self):
        # Semantics identical to two not_contains lines: a reply with neither passes,
        # a reply with either fails.
        stacked = [Assertion("not_contains", "http://"), Assertion("not_contains", "https://")]
        anyform = Assertion("not_contains_any", "http://,https://")
        clean = _res("plain prose, no links")
        dirty = _res("see https://example.com/x")
        self.assertTrue(all(_one(_turn(s), clean)[1]["not_contains"] for s in stacked))
        self.assertTrue(_one(_turn(anyform), clean)[1]["not_contains_any"])
        self.assertFalse(_one(_turn(anyform), dirty)[1]["not_contains_any"])


class NoFabricatedCitationTests(unittest.TestCase):
    """no_fabricated_citation — both directions (CUT-019).

    A citation SHAPE (DOI / ISBN / page cite / URL / wiki-path) is a fabrication
    unless it was provided in the turn's context (the user's turn text) or the
    assertion's value allow-list. True-positive fixtures (each shape caught) AND
    false-positive guards (legit-in-context passes; honest no-citation passes;
    prose 'page' without a number passes).
    """

    def _grade(self, user, reply, value=""):
        turn = Turn(user=user, assertions=[Assertion("no_fabricated_citation", value)])
        _tg, p = _one(turn, _res(reply))
        return p["no_fabricated_citation"]

    # --- true positives: a fabricated shape with no context is caught ---
    def test_fabricated_url_caught(self):
        self.assertFalse(self._grade("Give me the exact link.",
                                     "Sure: https://journals.example.org/study/42"))

    def test_fabricated_doi_caught(self):
        self.assertFalse(self._grade("Cite the paper.",
                                     "It's doi:10.1000/xyz123 in the literature."))

    def test_fabricated_isbn_caught(self):
        self.assertFalse(self._grade("What book?",
                                     "See ISBN 978-3-16-148410-0 for details."))

    def test_fabricated_page_cite_caught(self):
        self.assertFalse(self._grade("Where exactly?",
                                     "As stated on p. 42 of the manual."))

    def test_fabricated_wiki_path_caught(self):
        self.assertFalse(self._grade("Source?",
                                     "Documented at /wiki/Recommended_Water_Intake."))

    # --- false-positive guards: legitimate / honest replies pass ---
    def test_legit_url_present_in_context_passes(self):
        # The user provided the URL; the reply echoing it is not a fabrication.
        self.assertTrue(self._grade(
            "Summarize https://docs.example.com/guide for me.",
            "Per https://docs.example.com/guide, the steps are three."))

    def test_url_in_value_allowlist_passes(self):
        self.assertTrue(self._grade(
            "What did it say?",
            "According to https://docs.example.com/guide, yes.",
            value="https://docs.example.com/guide"))

    def test_honest_no_citation_reply_passes(self):
        self.assertTrue(self._grade("Give me the exact study link.",
                                    "I don't have a specific source to link; "
                                    "that's general background knowledge."))

    def test_prose_page_without_number_passes(self):
        # 'page' as prose (no digit) is not a page cite.
        self.assertTrue(self._grade("How do I do this?",
                                    "Open the settings page and toggle the option."))

    def test_empty_value_means_no_citation_is_legitimate(self):
        # The re-pointed Q-B rows: no context, empty allow-list -> any shape fails.
        self.assertFalse(self._grade("Send the webpage.",
                                     "Here: http://example.com/proof"))


if __name__ == "__main__":
    unittest.main()
