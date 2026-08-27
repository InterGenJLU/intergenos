# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
r"""A compound clause reaches its carrier, and a carrier that declines says so.

ROUND TWO of the uncarried-clause work. The first round taught the package
carrier to claim "find a pdf editor" and to answer "is X installed". A live
re-drive of the scenario corpus then showed the same class still open in three
more places, and this file closes all three. Every number below was measured on
this tree, not estimated.

1. RECOGNITION CANNOT CROSS A HYPHEN. The program-kind patterns are written
   `(?:a|an)\s+(?:\w+\s+){0,2}?<program-kind noun>`, and `\w` does not include
   "-". So "find a pdf editor" is recognised and "find a note-taking app" is
   not, although the two are the same ask about the same kind of thing.
   Measured at the base tree: of ten forms that must be recognised, SIX are
   not — "find a note-taking app", "find me a note-taking app", "get a
   note-taking app", "is there a note-taking app", "find a screen-recording
   tool" and "find an e-book reader" — while none of seven controls fires.
   The adjective is irrelevant: "find a note-taking app" has no adjective at
   all. The hyphen alone is the whole defect.

2. TWO RECOGNISED SHAPES BUILD NO ARGUMENTS, so they are recognised and then
   dropped — the exact shape round one existed to end, still open on two of the
   three phrasings the patterns admit. Measured at the base tree with NO hyphen
   anywhere, so this half is not a hyphen consequence:

       clause                     _match_keywords     _extract_arguments
       "find a pdf editor"        manage_packages     {"action": "search", …}
       "get a pdf editor"         manage_packages     None
       "get me a pdf editor"      manage_packages     None
       "is there a pdf editor"    manage_packages     None
       "is there a notes app"     manage_packages     None

   `_extract_package_search_term` requires a search verb (`search|find|look
   for`) by design, so that a raw sentence can never become the pkm query. "get"
   and "is there" are not search verbs, so the term comes back empty and the
   carrier declines. The recognition patterns admit three phrasings; the
   extractor understood one.

3. A CARRIER THAT DECLINES IS INDISTINGUISHABLE FROM NO CARRIER AT ALL.
   `_try_keyword_match` returns a bare `RouteResult(handled=False)` in three
   different situations — nothing matched, an intent matched with no tool, and a
   tool matched whose dispatch did not succeed — and emits nothing between them.
   Downstream, a failed dispatch and an unrecognised clause look identical, so
   the fail-safe behaviour the code documents for an indeterminate write ("the
   turn falls to a freeform clarify where InterGen ASKS what to write") can
   never be reached: nothing records that a carrier wanted the clause. This is
   the same class as the daemon fault whose only symptom was a silent fallback.

4. THE SCREEN-CAPTURE CARRIER HAS NO KEYWORD PATTERNS AT ALL. `take_screenshot`
   is a discovered, registered tool — it is in the registry beside
   manage_packages and web_search — and `_match_keywords` returns None for
   every phrasing of it measured at the base tree: "take a screenshot",
   "capture my screen", "screenshot my screen", "capture the screen", "use it
   to capture my screen", "use it to take a screenshot". So the second clause
   of "find a screenshot tool and use it to capture my screen" reaches no
   carrier while the FIRST clause dispatches manage_packages, which is exactly
   what the live re-drive recorded: tools_called=['manage_packages'] only.

5. A CONDITIONAL SUB-REQUEST'S CORPUS ASSERTION PRESUPPOSED ITS OWN BRANCH.
   The disk scenario asks to "delete the temp files IF it's over 80% full" and
   then asserted `gate_outcome: allow` — a terminal review-gate state that only
   exists when the delete actually runs. On a machine under 80% the delete
   correctly does not happen, no gate is ever held, and the grader reports
   "(never held)" as a FAILURE of the product. The assertion made the scenario
   depend on the disk of whatever box drove it. That is a corpus defect, not a
   product defect, and it is corrected here.

WHAT DECLINING NOW LOOKS LIKE. The keyword rung emits one glass row,
`decision/keyword_dispatch_declined`, naming the tool and one of three reasons —
`intent_without_tool`, `arguments_indeterminate`, `dispatch_failed` — and the
`RouteResult` it returns carries the same reason in `decline_reason`. The turn
still routes on exactly as before: `handled` stays False and no caller is
required to read the new field. This adds a witness; it changes no route.

TIER SCOPE. `_match_keywords` and `_extract_arguments` take no tier argument and
consult no tier state, so both defects and both fixes are identical on 2B, 9B and
35B. The tests are parametrized over all three anyway, driving the router the
daemon would build for that tier, so the claim is measured rather than asserted.

NAMED LIMITS. No embedding backend runs in this context, so registration logs
nine "Embedding intent … PENDING" lines and the SEMANTIC rung is inert — the
recognition proved here is the deterministic keyword rung, and whether the
embedding corpus would separately claim any of these clauses is not measured by
this file. Tool EXECUTION is stubbed at the tool-registry boundary: no pkm runs,
no package database is read, nothing is installed, and no screen is captured.
The MODEL rung is stubbed to yield nothing as well, so nothing here reaches a
model server even on a tier where dispatch is not locked — see `_router`.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from intergen import glass
from intergen.dispatch_policy import DispatchMode, resolve_dispatch_for_model
from intergen.interfaces.types import HardwareTierLevel, ToolResult
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tests import glass_rows
from intergen.tool_registry import ToolRegistry

TIERS = (
    ("2B", HardwareTierLevel.TIER_1),
    ("9B", HardwareTierLevel.TIER_2),
    ("35B", HardwareTierLevel.TIER_3),
)


def locked_for(tier: HardwareTierLevel) -> bool:
    return resolve_dispatch_for_model(
        tier, detected_tier=tier).dispatch_mode is DispatchMode.LOCKED_DOWN


class _Resp:
    """A completion carrying only what the freeform path reads."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.quality_passed = True
        self.escalated = False
        self.local = True
        self.model = "stub"
        self.tokens_prompt = 0
        self.tokens_completion = 0
        self.semantic_flags = []


def _router(tier: HardwareTierLevel, *, replies=(), tool_succeeds: bool = True):
    """The router the daemon would build for this tier, with tool EXECUTION
    stubbed at the registry boundary.

    `tool_succeeds=False` makes every dispatch come back unsuccessful, which is
    the third decline reason and cannot otherwise be produced without running a
    real tool and making it fail. Returns (router, dispatched).
    """
    from intergen.intents import register_all_intents
    reg = ToolRegistry()
    reg.discover_tools()
    matcher = SemanticMatcher(embedder=None)
    register_all_intents(matcher)
    r = ConversationRouter(
        tool_registry=reg, semantic_matcher=matcher, llm=LLMRouter(config=None),
        lock_dispatch=locked_for(tier), hardware_tier=tier)

    dispatched = []

    def _execute(call, **kw):
        dispatched.append(call)
        return ToolResult(
            call_id=getattr(call, "call_id", ""), name=call.name,
            content="stubbed tool output" if tool_succeeds else "",
            success=tool_succeeds)

    reg.execute = _execute

    seq = list(replies)
    calls = {"n": 0}

    def _chat(messages, **kw):
        calls["n"] += 1
        return seq[min(calls["n"] - 1, len(seq) - 1)] if seq else _Resp("")

    r._llm.chat = _chat

    # THE MODEL RUNG IS STUBBED TO YIELD NOTHING, AND THAT IS NOT COSMETIC.
    # `_try_llm_tools` calls `self._llm.stream_with_tools`, which POSTs to
    # 127.0.0.1:8080 — so on a tier where dispatch is NOT locked (the 9B), a
    # fixture that leaves it alone reaches whatever model happens to be serving
    # on the box running the tests. Measured: with a live server up, "how do I
    # take a screenshot" came back as a take_screenshot CALL from the model, and
    # a negative control that is really asking the model a question proves
    # nothing about this lane and does not reproduce. Stubbed to an empty
    # iterator so every assertion here is about the DETERMINISTIC rungs, and so
    # the file gives the same answer on a box with no engine at all.
    r._llm.stream_with_tools = lambda messages, **kw: iter(())
    return r, dispatched


def _route_clause(r, clause: str):
    """Route one clause the way `_handle_compound` reaches `_route_single`.

    `_route_impl` sets `_current_query_type` before routing and
    `_try_llm_freeform` reads it, so a direct `_route_single` call without it
    raises AttributeError — a red that fires on the test's own wiring rather
    than on the product.
    """
    r._current_query_type = r._classify_query_type(clause)
    r._route_trail = []
    return r._route_single(clause, trail_scope="sub_query:1")


# ── The clause family, with the arguments each one's carrier must be handed ──
# The hyphenated members are defect 1; the "get"/"is there" members are defect 2;
# the plain "find" members already worked in round one and are kept as controls
# that the fix does not disturb.
HYPHENATED = (
    ("find a note-taking app",
     {"action": "search", "query": "note-taking app"}),
    # WRT-do-for-me-02's own first clause: an adjective AND a hyphen, which puts
    # the filler group at its cap of two tokens ("good", "note-taking").
    ("find a good note-taking app",
     {"action": "search", "query": "good note-taking app"}),
    ("find me a note-taking app",
     {"action": "search", "query": "note-taking app"}),
    ("find a screen-recording tool",
     {"action": "search", "query": "screen-recording tool"}),
    ("find an e-book reader",
     {"action": "search", "query": "e-book reader"}),
)
GET_AND_IS_THERE = (
    ("get a pdf editor",
     {"action": "search", "query": "pdf editor"}),
    ("get me a pdf editor",
     {"action": "search", "query": "pdf editor"}),
    ("is there a pdf editor",
     {"action": "search", "query": "pdf editor"}),
    ("is there a notes app",
     {"action": "search", "query": "notes app"}),
    ("get a note-taking app",
     {"action": "search", "query": "note-taking app"}),
    ("is there a note-taking app",
     {"action": "search", "query": "note-taking app"}),
)
ALREADY_CARRIED = (
    ("find a pdf editor",
     {"action": "search", "query": "pdf editor"}),
    ("find a notes app",
     {"action": "search", "query": "notes app"}),
    ("find me a photo editor",
     {"action": "search", "query": "photo editor"}),
)
ALL_CARRIED = HYPHENATED + GET_AND_IS_THERE + ALREADY_CARRIED


class AHyphenatedProgramKindIsRecognised(unittest.TestCase):
    """RED at base for the hyphenated forms: `\\w` cannot match "-", so the
    filler group cannot cross "note-taking" and the pattern never reaches the
    program-kind noun."""

    def test_the_package_carrier_claims_a_hyphenated_ask(self) -> None:
        for name, tier in TIERS:
            r, _ = _router(tier)
            for clause, _args in HYPHENATED:
                with self.subTest(tier=name, clause=clause):
                    m = r._semantic._match_keywords(clause)
                    self.assertEqual(
                        m.intent_id, "manage_packages",
                        f"[{name}] {clause!r} is an ask for SOFTWARE and must be "
                        f"recognised by the package carrier; a hyphen inside the "
                        f"kind of program is not a reason to leave it for a "
                        f"model turn that has no tools")
                    self.assertEqual(m.tool_name, "manage_packages")

    def test_the_hyphen_is_the_whole_defect_not_the_adjective(self) -> None:
        """"find a note-taking app" carries no adjective at all, so an
        explanation that blames the adjective count is refuted here."""
        r, _ = _router(HardwareTierLevel.TIER_3)
        plain = r._semantic._match_keywords("find a notes app")
        hyphen = r._semantic._match_keywords("find a note-taking app")
        self.assertEqual(plain.intent_id, "manage_packages")
        self.assertEqual(
            hyphen.intent_id, plain.intent_id,
            "the two asks differ only by a hyphen inside the program kind")


class EveryRecognisedShapeBuildsItsArguments(unittest.TestCase):
    """RED at base for the "get"/"is there" forms: recognised, and then dropped
    because `_extract_package_search_term` requires a search verb."""

    def test_get_and_is_there_extract_the_same_term_find_does(self) -> None:
        for name, tier in TIERS:
            r, _ = _router(tier)
            for clause, args in GET_AND_IS_THERE:
                with self.subTest(tier=name, clause=clause):
                    self.assertEqual(
                        r._extract_arguments("manage_packages", clause), args,
                        f"[{name}] {clause!r} is recognised by the package "
                        f"carrier, so it must also build arguments; a clause "
                        f"recognised and then dropped is the defect round one "
                        f"closed for the 'find' phrasing")

    def test_the_hyphenated_forms_extract_their_term(self) -> None:
        for name, tier in TIERS:
            r, _ = _router(tier)
            for clause, args in HYPHENATED:
                with self.subTest(tier=name, clause=clause):
                    self.assertEqual(
                        r._extract_arguments("manage_packages", clause), args)

    def test_the_round_one_forms_are_untouched(self) -> None:
        """GREEN at base and after: the fix must not move what already worked."""
        for name, tier in TIERS:
            r, _ = _router(tier)
            for clause, args in ALREADY_CARRIED:
                with self.subTest(tier=name, clause=clause):
                    self.assertEqual(
                        r._extract_arguments("manage_packages", clause), args)


class TheClauseReachesItsCarrierEndToEnd(unittest.TestCase):
    """RED at base: with no carrier, the clause lands in a freeform turn."""

    def test_every_clause_dispatches_instead_of_falling_to_freeform(self) -> None:
        for name, tier in TIERS:
            for clause, args in ALL_CARRIED:
                with self.subTest(tier=name, clause=clause):
                    r, dispatched = _router(tier)
                    res = _route_clause(r, clause)
                    self.assertTrue(
                        res.handled,
                        f"[{name}] {clause!r} must be answered by its carrier")
                    self.assertEqual(
                        [c.name for c in dispatched], ["manage_packages"],
                        f"[{name}] {clause!r} must dispatch the package tool "
                        f"exactly once")
                    self.assertEqual(dispatched[0].arguments, args)


class TheNegativeSetIsUnchanged(unittest.TestCase):
    """A control that does not fire proves nothing about the pattern and
    everything about the fixture, so these are the shapes the widened token
    class could plausibly have swallowed."""

    NOT_SOFTWARE = (
        "find my car keys",
        "find a good movie to watch",
        "what is a pdf editor",
        "how do I edit a pdf",
        "tell me about pdf editors",
        "find the largest files",
        "find hidden files in my home directory",
        "is there a reason my laptop is slow",
        "find a way to speed this up",
    )

    def test_none_of_them_is_claimed_by_the_package_carrier(self) -> None:
        for name, tier in TIERS:
            r, _ = _router(tier)
            for clause in self.NOT_SOFTWARE:
                with self.subTest(tier=name, clause=clause):
                    m = r._semantic._match_keywords(clause)
                    self.assertNotEqual(
                        m.intent_id, "manage_packages",
                        f"[{name}] {clause!r} is not an ask for software and "
                        f"must not become a package search")

    def test_none_of_them_dispatches_a_package_tool(self) -> None:
        for name, tier in TIERS:
            for clause in self.NOT_SOFTWARE:
                with self.subTest(tier=name, clause=clause):
                    r, dispatched = _router(tier)
                    _route_clause(r, clause)
                    self.assertNotIn(
                        "manage_packages", [c.name for c in dispatched],
                        f"[{name}] {clause!r} must not reach the package tool")


class ADecliningCarrierIsDistinguishable(unittest.TestCase):
    """RED at base: all three declines return a bare `RouteResult(handled=False)`
    and emit nothing, so a failed dispatch reads exactly like a clause nothing
    claimed."""

    def _rows(self, tmp: str):
        return glass_rows.read(tmp)

    def _fresh_glass(self, tmp: str) -> None:
        os.environ["XDG_STATE_HOME"] = tmp
        os.environ.pop("INTERGEN_GLASS", None)
        glass._glass = None

    def test_a_failed_dispatch_says_so(self) -> None:
        """A tool the carrier chose, executed, that came back unsuccessful."""
        with tempfile.TemporaryDirectory() as tmp:
            self._fresh_glass(tmp)
            r, dispatched = _router(HardwareTierLevel.TIER_3,
                                    tool_succeeds=False)
            res = r._try_keyword_match("find a pdf editor")
            self.assertFalse(res.handled, "an unsuccessful dispatch is not handled")
            self.assertEqual(
                res.decline_reason, "dispatch_failed",
                "the caller must be able to tell a failed dispatch from a "
                "clause no carrier claimed")
            self.assertEqual([c.name for c in dispatched], ["manage_packages"])
            row = glass_rows.only(self._rows(tmp), phase="decision",
                                  event="keyword_dispatch_declined")
            self.assertEqual(row["detail"]["tool"], "manage_packages")
            self.assertEqual(row["detail"]["reason"], "dispatch_failed")

    def test_indeterminate_arguments_say_so(self) -> None:
        """A carrier that recognises the clause and can build no arguments.

        "save this to /tmp/notes.txt" IS claimed by the write carrier — the
        pattern reads a verb, a span, and a path — and its extractor returns
        None, because "this" is a bare context-referencing token and writing
        the literal word "this" into a person's file is a silent wrong write.
        That refusal is correct and is not what this asserts. What this asserts
        is that the refusal leaves a witness, so the clarify the refusal
        documents ("what should I write to <path>?") has something to act on.

        Nothing is written: the extractor declines before any dispatch, and the
        tool registry is stubbed in this fixture regardless.
        """
        with tempfile.TemporaryDirectory() as tmp:
            self._fresh_glass(tmp)
            r, dispatched = _router(HardwareTierLevel.TIER_3)
            self.assertEqual(
                r._semantic._match_keywords(
                    "save this to /tmp/notes.txt").intent_id, "write_file",
                "the fixture depends on this clause being RECOGNISED")
            self.assertIsNone(
                r._extract_arguments("write_file", "save this to /tmp/notes.txt"),
                "the fixture depends on its arguments being indeterminate")
            res = r._try_keyword_match("save this to /tmp/notes.txt")
            self.assertFalse(res.handled)
            self.assertEqual(res.decline_reason, "arguments_indeterminate")
            self.assertEqual(dispatched, [],
                             "nothing may be dispatched without arguments")
            row = glass_rows.only(self._rows(tmp), phase="decision",
                                  event="keyword_dispatch_declined")
            self.assertEqual(row["detail"]["tool"], "write_file")
            self.assertEqual(row["detail"]["reason"], "arguments_indeterminate")

    def test_an_unrecognised_clause_is_named_as_such(self) -> None:
        """The ordinary case still returns handled=False, and now says which of
        the three it was rather than leaving the caller to guess."""
        with tempfile.TemporaryDirectory() as tmp:
            self._fresh_glass(tmp)
            r, dispatched = _router(HardwareTierLevel.TIER_3)
            res = r._try_keyword_match("find my car keys")
            self.assertFalse(res.handled)
            self.assertEqual(res.decline_reason, "no_intent")
            self.assertEqual(dispatched, [])

    def test_the_witness_does_not_change_the_route(self) -> None:
        """`handled` is still False in every decline, so no caller's control
        flow moves because of this lane."""
        with tempfile.TemporaryDirectory() as tmp:
            self._fresh_glass(tmp)
            for clause, succeeds in (("find my car keys", True),
                                     ("find a pdf editor", False)):
                with self.subTest(clause=clause):
                    r, _ = _router(HardwareTierLevel.TIER_3,
                                   tool_succeeds=succeeds)
                    self.assertFalse(r._try_keyword_match(clause).handled)

    def test_a_successful_dispatch_carries_no_decline_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._fresh_glass(tmp)
            r, dispatched = _router(HardwareTierLevel.TIER_3)
            res = r._try_keyword_match("find a pdf editor")
            self.assertTrue(res.handled)
            self.assertEqual(res.decline_reason, "")
            self.assertEqual([c.name for c in dispatched], ["manage_packages"])
            self.assertEqual(
                glass_rows.where(self._rows(tmp), phase="decision",
                                 event="keyword_dispatch_declined"), [],
                "a dispatch that succeeded may not emit a decline row")


# The screen-capture clause family. The first clause of WRT-do-for-me-07 already
# reaches manage_packages; it is listed here as the control that the second
# clause's new carrier does not steal it.
SCREEN_CAPTURE = (
    "take a screenshot",
    "capture my screen",
    "capture the screen",
    "screenshot my screen",
    "use it to capture my screen",
    "use it to take a screenshot",
    "take a screenshot of my screen",
)


class TheScreenCaptureCarrierClaimsItsClause(unittest.TestCase):
    """RED at base: `take_screenshot` is a registered tool with no keyword
    patterns, so every phrasing of it falls through to a model turn."""

    def test_every_capture_phrasing_reaches_the_capture_tool(self) -> None:
        for name, tier in TIERS:
            r, _ = _router(tier)
            for clause in SCREEN_CAPTURE:
                with self.subTest(tier=name, clause=clause):
                    m = r._semantic._match_keywords(clause)
                    self.assertEqual(
                        m.intent_id, "take_screenshot",
                        f"[{name}] {clause!r} asks the machine to capture the "
                        f"screen, and the tool that does it is registered; a "
                        f"clause with a real carrier must not reach a model "
                        f"turn that has no tools")
                    self.assertEqual(m.tool_name, "take_screenshot")

    def test_the_find_clause_beside_it_keeps_the_package_carrier(self) -> None:
        """The compound's FIRST clause must not be swallowed by the new
        patterns: "find a screenshot tool" is an ask for SOFTWARE."""
        for name, tier in TIERS:
            r, _ = _router(tier)
            for clause in ("find a screenshot tool",
                           "find a screen-recording tool",
                           "is there a screenshot tool"):
                with self.subTest(tier=name, clause=clause):
                    m = r._semantic._match_keywords(clause)
                    self.assertEqual(
                        m.intent_id, "manage_packages",
                        f"[{name}] {clause!r} asks for a PROGRAM, not for a "
                        f"capture")

    def test_a_question_about_capturing_does_not_capture(self) -> None:
        """A question is not an instruction. None of these may dispatch."""
        for name, tier in TIERS:
            for clause in ("how do I take a screenshot",
                           "what is the screenshot key",
                           "can you take screenshots",
                           "where do my screenshots go"):
                with self.subTest(tier=name, clause=clause):
                    r, dispatched = _router(tier)
                    m = r._semantic._match_keywords(clause)
                    self.assertNotEqual(
                        m.intent_id, "take_screenshot",
                        f"[{name}] {clause!r} is a QUESTION about capturing "
                        f"and must not capture anything")
                    _route_clause(r, clause)
                    self.assertNotIn("take_screenshot",
                                     [c.name for c in dispatched])

    def test_the_capture_clause_dispatches_end_to_end(self) -> None:
        for name, tier in TIERS:
            for clause in SCREEN_CAPTURE:
                with self.subTest(tier=name, clause=clause):
                    r, dispatched = _router(tier)
                    res = _route_clause(r, clause)
                    self.assertTrue(res.handled, f"[{name}] {clause!r}")
                    self.assertEqual([c.name for c in dispatched],
                                     ["take_screenshot"])


class TheConditionalDeleteScenarioDoesNotPresupposeItsBranch(unittest.TestCase):
    """RED at base: the shipped corpus asserts `gate_outcome: allow` on a turn
    whose delete only runs when the disk is over 80% full, so the scenario
    grades the machine it happens to run on."""

    SCENARIO_ID = "WRT-do-for-me-10"

    def _scenario(self) -> dict:
        import json
        from pathlib import Path
        corpus = (Path(__file__).resolve().parent / "scenario" / "corpus"
                  / "writing_help.json")
        for s in json.loads(corpus.read_text(encoding="utf-8")):
            if s.get("id") == self.SCENARIO_ID:
                return s
        self.fail(f"{self.SCENARIO_ID} is not in the shipped corpus")

    def test_no_assertion_requires_a_branch_the_condition_may_not_take(
            self) -> None:
        scenario = self._scenario()
        turn = scenario["turns"][0]
        self.assertIn("if", turn["user"].lower(),
                      "the fixture depends on this turn being conditional")
        for a in turn["assertions"]:
            self.assertNotEqual(
                a["type"], "gate_outcome",
                "gate_outcome asserts the TERMINAL STATE of a review gate, "
                "which exists only if the gated dispatch actually ran; on a "
                "conditional sub-request whose condition is false the gate is "
                "never held and the grader reports '(never held)' as a product "
                "failure. A scenario may not depend on the disk of the box "
                "that drives it")

    def test_the_destructive_contract_is_still_asserted(self) -> None:
        """Removing the presupposition must not remove the safety it stood
        for: the reply may still never CLAIM a delete that no successful
        dispatch backs."""
        types = {a["type"] for a in self._scenario()["turns"][0]["assertions"]}
        self.assertIn(
            "no_fabricated_success", types,
            "the conditional delete's real invariant is that a completed-action "
            "claim needs a matching successful dispatch")

    def test_the_scenario_still_asserts_its_decomposition(self) -> None:
        turn = self._scenario()["turns"][0]
        kinds = {a["type"]: a["value"] for a in turn["assertions"]}
        self.assertEqual(kinds.get("decomposes_into"), "2")
        self.assertEqual(kinds.get("uses_any_tool"), "run_command")


class TheFixReadsNoTier(unittest.TestCase):
    """Both halves are pure string work, so all three tiers must agree."""

    def test_recognition_and_extraction_agree_across_the_three_tiers(self) -> None:
        for clause, args in ALL_CARRIED:
            with self.subTest(clause=clause):
                seen_intent = set()
                seen_args = set()
                for _name, tier in TIERS:
                    r, _ = _router(tier)
                    seen_intent.add(r._semantic._match_keywords(clause).intent_id)
                    seen_args.add(
                        repr(r._extract_arguments("manage_packages", clause)))
                self.assertEqual(seen_intent, {"manage_packages"})
                self.assertEqual(seen_args, {repr(args)})


if __name__ == "__main__":
    unittest.main()
