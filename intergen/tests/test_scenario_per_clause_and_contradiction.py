# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A compound turn is graded clause by clause, and a reply may not contradict itself.

TWO GAPS MEASURED ON THE 2B, 2026-08-26. Four scenarios of the shape "find X and
use it to Y" were driven at the tree tip. Two of them were graded PASS while the
second clause never dispatched at all, and one of the passing replies stated a
fact and its negation in the same answer. Both passes were correct against the
assertions as written, which is what makes them findings about the HARNESS.

GAP ONE — `uses_ANY_tool` cannot see a clause. A compound request decomposes
into sub-queries and each is routed on its own, but every assertion in the
harness reads the turn's FLAT tool list. So "find a note-taking app and use it
to capture my screen" is satisfied by one dispatch for clause 1 while clause 2
only talks, and the grade cannot say which half was served. `uses_tool_for_clause`
keys the expectation to the sub-query INDEX, so a turn that serves clause 1 and
narrates clause 2 fails, and the failure NAMES the clause that was not served.

  THE ATTRIBUTION HAD TO BE EMITTED BEFORE IT COULD BE ASSERTED. The router
  emitted a `prompt`/`subquery` row per clause carrying its index, its text and
  its route source — but not the tools that clause dispatched, because
  `all_tool_calls.extend(sub_result.tool_calls)` flattens them into one list
  before anything is written down. The attribution existed only inside the loop
  and was discarded at the end of it. Asserting a per-clause tool without that
  emission would mean guessing which dispatch belonged to which clause from
  ordering, and a grade built on a guess is worse than no grade. So the row now
  carries the clause's own tool names, the trace carries them through, and the
  assertion FAILS CLOSED when no such attribution was joined — the same
  discipline `decomposes_into` already applies to a missing decomposition trace.

GAP TWO — nothing reads a reply for self-contradiction. One 2B reply said docker
was "not installed" and, further down, that it was "already installed". The
existing `self_consistent` assertion catches exactly one shape — enumerating
items while claiming none were found — and cannot see this one.
`no_self_contradiction` names a subject and fails when the reply asserts both a
positive and a negative state about it. A decomposed answer is where this
happens, because each clause is answered separately and nothing reconciles them.

WHY THE NEGATIVE DIRECTION IS FIRST IN EVERY CLASS BELOW. An assertion that
cannot fail grades nothing, and both of these exist because something that could
not fail was reported as a pass.
"""

from __future__ import annotations

import unittest

from intergen.tests.scenario import grader, schema
from intergen.tests.scenario.schema import Assertion, Turn
from intergen.tests.scenario.trace import ToolDispatch, TraceView
from intergen.tests.scenario.transport import TurnResult


def _trace(*, sub_queries=(), clause_tools=None, joined=True, dispatches=()):
    """A trace shaped like the one the runner joins for a decomposed turn."""
    view = TraceView(
        trace_id="t-1",
        route_source="decomposed",
        sub_queries=list(sub_queries),
        decomposition_source_joined=joined,
        dispatches=[ToolDispatch(name=n) for n in dispatches],
    )
    if clause_tools is not None:
        view.sub_query_tools = {int(k): list(v) for k, v in clause_tools.items()}
        view.subquery_attribution_joined = True
    return view


def _grade_clause(index, tool, trace, user="find a note-taking app and use it to capture my screen"):
    turn = Turn(user=user, assertions=[
        Assertion(type="uses_tool_for_clause", value=tool,
                  params={"index": index},
                  description=f"clause {index} dispatches {tool}")])
    return grader.grade_turn(turn, TurnResult(text="ok", source="decomposed"),
                             trace, category="conversational")


def _grade_contradiction(reply, subject):
    turn = Turn(user="is docker installed, and install it if not", assertions=[
        Assertion(type="no_self_contradiction", value=subject,
                  description="the reply does not state a fact and its negation")])
    return grader.grade_turn(turn, TurnResult(text=reply, source="decomposed"),
                             None, category="conversational")


def _only(tg, atype):
    return next(r for r in tg.results if r.type == atype)


# ── registration ────────────────────────────────────────────────────────────

class BothAssertionsAreRegistered(unittest.TestCase):

    def test_the_schema_knows_uses_tool_for_clause(self):
        self.assertIn("uses_tool_for_clause", schema.ASSERTION_TYPES)

    def test_the_schema_knows_no_self_contradiction(self):
        self.assertIn("no_self_contradiction", schema.ASSERTION_TYPES)

    def test_the_grader_can_evaluate_uses_tool_for_clause(self):
        self.assertIn("uses_tool_for_clause", grader._EXPLICIT_EVALUATORS)

    def test_the_grader_can_evaluate_no_self_contradiction(self):
        self.assertIn("no_self_contradiction", grader._EXPLICIT_EVALUATORS)

    def test_both_are_gate_a(self):
        """Neither is phrasing. A clause that never dispatched, and an answer
        that contradicts itself, are correctness failures."""
        self.assertEqual(grader.gate_for("uses_tool_for_clause"), "A")
        self.assertEqual(grader.gate_for("no_self_contradiction"), "A")


# ── uses_tool_for_clause ────────────────────────────────────────────────────

class TheClauseAssertionFails(unittest.TestCase):

    def test_the_measured_case_a_served_clause_one_and_a_narrated_clause_two(self):
        """The exact 2026-08-26 shape: clause 1 dispatched, clause 2 did not."""
        tr = _trace(
            sub_queries=["find a note-taking app", "use it to capture my screen"],
            clause_tools={1: ["manage_packages"], 2: []},
            dispatches=["manage_packages"])
        res = _only(_grade_clause(2, "screen_capture", tr), "uses_tool_for_clause")
        self.assertFalse(res.passed,
                         "clause 2 dispatched nothing and the assertion passed")

    def test_the_failure_names_the_clause_and_its_text(self):
        """A failure that does not say WHICH half was unserved is half a report."""
        tr = _trace(
            sub_queries=["find a note-taking app", "use it to capture my screen"],
            clause_tools={1: ["manage_packages"], 2: []},
            dispatches=["manage_packages"])
        res = _only(_grade_clause(2, "screen_capture", tr), "uses_tool_for_clause")
        self.assertIn("2", res.actual)
        self.assertIn("capture my screen", res.actual.lower())

    def test_a_flat_tool_list_does_not_rescue_the_wrong_clause(self):
        """The whole point. The tool WAS called — for the other clause."""
        tr = _trace(
            sub_queries=["find a note-taking app", "use it to capture my screen"],
            clause_tools={1: ["screen_capture"], 2: []},
            dispatches=["screen_capture"])
        res = _only(_grade_clause(2, "screen_capture", tr), "uses_tool_for_clause")
        self.assertFalse(
            res.passed,
            "the tool was dispatched for clause 1 and the assertion credited "
            "clause 2 with it — this is the flat-list defect the assertion exists "
            "to close")

    def test_an_index_past_the_end_fails_rather_than_passing_vacuously(self):
        tr = _trace(sub_queries=["only one clause"], clause_tools={1: ["x"]})
        res = _only(_grade_clause(2, "x", tr), "uses_tool_for_clause")
        self.assertFalse(res.passed)

    def test_no_attribution_joined_fails_closed_and_says_so(self):
        """Nothing was read that could answer the question, so nothing passes.

        The distinction `decomposes_into` already draws: 'the clause dispatched
        nothing' and 'no source attested what any clause dispatched' are
        different facts and must not be reported as one.
        """
        tr = _trace(sub_queries=["a", "b"], clause_tools=None,
                    dispatches=["manage_packages"])
        res = _only(_grade_clause(2, "screen_capture", tr), "uses_tool_for_clause")
        self.assertFalse(res.passed)
        self.assertIn("attribution", res.actual.lower())

    def test_no_trace_at_all_fails_closed(self):
        res = _only(_grade_clause(1, "screen_capture", None), "uses_tool_for_clause")
        self.assertFalse(res.passed)


class TheClauseAssertionPasses(unittest.TestCase):

    def test_a_clause_that_dispatched_its_tool_passes(self):
        tr = _trace(
            sub_queries=["find a note-taking app", "use it to capture my screen"],
            clause_tools={1: ["manage_packages"], 2: ["screen_capture"]},
            dispatches=["manage_packages", "screen_capture"])
        self.assertTrue(
            _only(_grade_clause(2, "screen_capture", tr), "uses_tool_for_clause").passed)

    def test_both_clauses_can_be_asserted_on_one_turn(self):
        """The reason the assertion is indexed: a compound turn asserts BOTH."""
        tr = _trace(
            sub_queries=["find a note-taking app", "use it to capture my screen"],
            clause_tools={1: ["manage_packages"], 2: ["screen_capture"]},
            dispatches=["manage_packages", "screen_capture"])
        turn = Turn(user="find a note-taking app and use it to capture my screen",
                    assertions=[
                        Assertion(type="uses_tool_for_clause", value="manage_packages",
                                  params={"index": 1}),
                        Assertion(type="uses_tool_for_clause", value="screen_capture",
                                  params={"index": 2})])
        tg = grader.grade_turn(turn, TurnResult(text="ok", source="decomposed"),
                               tr, category="conversational")
        got = [r for r in tg.results if r.type == "uses_tool_for_clause"]
        self.assertEqual(len(got), 2)
        self.assertTrue(all(r.passed for r in got))


# ── no_self_contradiction ───────────────────────────────────────────────────

class TheContradictionAssertionFails(unittest.TestCase):

    def test_the_measured_reply_not_installed_and_already_installed(self):
        """The 2026-08-26 2B reply, in substance."""
        reply = ("**1.** Docker is not installed on your system.\n\n"
                 "**2.** Docker is already installed, so there is nothing to do.")
        res = _only(_grade_contradiction(reply, "docker"), "no_self_contradiction")
        self.assertFalse(res.passed,
                         "a reply that said docker was both absent and present "
                         "was graded consistent")

    def test_the_failure_quotes_both_halves(self):
        reply = ("**1.** Docker is not installed on your system.\n\n"
                 "**2.** Docker is already installed, so there is nothing to do.")
        res = _only(_grade_contradiction(reply, "docker"), "no_self_contradiction")
        low = res.actual.lower()
        self.assertIn("not installed", low)
        self.assertIn("already installed", low)

    def test_is_not_running_and_is_running(self):
        reply = ("**1.** The service is not running.\n\n"
                 "**2.** The service is running normally.")
        self.assertFalse(
            _only(_grade_contradiction(reply, "service"), "no_self_contradiction").passed)

    def test_found_none_and_found_some(self):
        reply = ("**1.** No printers were found.\n\n"
                 "**2.** Your printer is available and ready.")
        self.assertFalse(
            _only(_grade_contradiction(reply, "printer"), "no_self_contradiction").passed)


class TheContradictionAssertionPasses(unittest.TestCase):

    def test_a_consistent_reply_passes(self):
        reply = ("**1.** Docker is not installed on your system.\n\n"
                 "**2.** I can install it for you if you would like.")
        self.assertTrue(
            _only(_grade_contradiction(reply, "docker"), "no_self_contradiction").passed)

    def test_a_reply_about_a_different_subject_is_not_a_contradiction(self):
        """Precision matters: Gate A is hard, so a false failure is expensive.

        Two subjects, opposite states, one reply — and nothing wrong with it.
        """
        reply = ("**1.** Docker is not installed.\n\n"
                 "**2.** Podman is installed and running.")
        self.assertTrue(
            _only(_grade_contradiction(reply, "docker"), "no_self_contradiction").passed)

    def test_a_state_that_changed_within_the_turn_is_not_a_contradiction(self):
        """"It was not installed, so I installed it" is a sequence, not a
        contradiction. A grader that cannot tell the difference would fail every
        successful install."""
        reply = ("Docker was not installed, so I installed it. "
                 "Docker is now installed and ready.")
        self.assertTrue(
            _only(_grade_contradiction(reply, "docker"), "no_self_contradiction").passed)

    def test_an_empty_reply_does_not_contradict_itself(self):
        self.assertTrue(
            _only(_grade_contradiction("", "docker"), "no_self_contradiction").passed)


# ── the emission the assertion rests on ─────────────────────────────────────

class TheRouterAttributesToolsToClauses(unittest.TestCase):
    """Without this row the assertion above would be grading a guess."""

    def test_the_subquery_row_carries_the_clauses_own_tools(self):
        import ast
        import inspect
        from intergen import router

        tree = ast.parse(inspect.getsource(router))
        emits = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "emit"
                 and len(n.args) >= 2
                 and isinstance(n.args[1], ast.Constant)
                 and n.args[1].value == "subquery"]
        self.assertEqual(len(emits), 1,
                         "expected exactly one prompt/subquery emission in the "
                         "router; this test must be re-pointed")
        detail = next((k.value for k in emits[0].keywords if k.arg == "detail"), None)
        self.assertIsInstance(detail, ast.Dict,
                              "the subquery row's detail is not a literal dict")
        keys = [k.value for k in detail.keys if isinstance(k, ast.Constant)]
        self.assertIn(
            "tools", keys,
            "the per-clause row does not carry the tools that clause dispatched, "
            "so no trace can attribute a dispatch to a clause and the per-clause "
            "assertion would be grading an ordering guess")


class TheTraceCarriesTheAttribution(unittest.TestCase):

    def test_a_trace_view_has_somewhere_to_put_it(self):
        view = TraceView()
        self.assertEqual(view.sub_query_tools, {})
        self.assertFalse(view.subquery_attribution_joined)

    def test_glass_rows_populate_it(self):
        rows = [
            {"phase": "decision", "event": "compound_route",
             "detail": {"sub_queries": ["find a note-taking app",
                                        "use it to capture my screen"]}},
            {"phase": "prompt", "event": "subquery",
             "detail": {"index": 1, "of": 2, "sub_query": "find a note-taking app",
                        "source": "keyword", "tools": ["manage_packages"]}},
            {"phase": "prompt", "event": "subquery",
             "detail": {"index": 2, "of": 2,
                        "sub_query": "use it to capture my screen",
                        "source": "llm", "tools": []}},
        ]
        view = TraceView.from_glass_rows(rows)
        self.assertTrue(view.subquery_attribution_joined)
        self.assertEqual(view.sub_query_tools, {1: ["manage_packages"], 2: []})


class TheHarnessActuallyLoadsTheRow(unittest.TestCase):
    """The emission and the trace are not enough: the loader has to keep the row.

    MEASURED 2026-08-27, on the first live re-drive of this cut. The router
    emitted the per-clause rows and the TraceView knew how to read them, but
    live_run's glass loader keeps only a fixed set of phase/event pairs and that
    set did not list this one. The rows were dropped before the trace was built,
    so six per-clause assertions failed closed — each reporting that nothing had
    been read that could say what a clause dispatched, about an attribution the
    product had in fact written down.

    That is the honest behaviour of a fail-closed assertion and it is exactly why
    the failure was legible rather than a false pass. It is also a coupling that
    nothing checked, which is what this class is.
    """

    def test_the_loader_keeps_the_per_clause_row(self):
        import json
        import tempfile
        from pathlib import Path

        from intergen.tests.scenario import live_run

        rows = [
            {"turn_id": "t1", "phase": "route", "event": "decided",
             "detail": {"source": "decomposed"}},
            {"turn_id": "t1", "phase": "prompt", "event": "subquery",
             "detail": {"index": 1, "of": 2, "sub_query": "find one",
                        "tools": ["web_search"]}},
            {"turn_id": "t1", "phase": "prompt", "event": "token",
             "detail": {"noise": "a row the harness does not read"}},
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "glass.jsonl"
            p.write_text("".join(json.dumps(r) + "\n" for r in rows))
            kept = live_run.load_glass_rows(p)

        pairs = {(r.get("phase"), r.get("event")) for r in kept}
        self.assertIn(
            ("prompt", "subquery"), pairs,
            "the glass loader dropped the per-clause dispatch row, so the "
            "attribution never reaches the trace and every per-clause assertion "
            "fails closed on a live run")
        self.assertNotIn(("prompt", "token"), pairs,
                         "the loader stopped filtering; reading every row of an "
                         "always-on log is the memory hazard the filter exists for")

    def test_a_loaded_row_reaches_the_trace(self):
        """End to end: rows the loader keeps must populate the attribution."""
        import json
        import tempfile
        from pathlib import Path

        from intergen.tests.scenario import live_run

        rows = [
            {"turn_id": "t1", "phase": "decision", "event": "compound_route",
             "detail": {"sub_queries": ["find one", "use it"]}},
            {"turn_id": "t1", "phase": "prompt", "event": "subquery",
             "detail": {"index": 1, "of": 2, "sub_query": "find one",
                        "tools": ["web_search"]}},
            {"turn_id": "t1", "phase": "prompt", "event": "subquery",
             "detail": {"index": 2, "of": 2, "sub_query": "use it",
                        "tools": []}},
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "glass.jsonl"
            p.write_text("".join(json.dumps(r) + "\n" for r in rows))
            view = TraceView.from_glass_rows(live_run.load_glass_rows(p),
                                             trace_id="t1")

        self.assertTrue(view.subquery_attribution_joined)
        self.assertEqual(view.sub_query_tools, {1: ["web_search"], 2: []})


if __name__ == "__main__":
    unittest.main()
