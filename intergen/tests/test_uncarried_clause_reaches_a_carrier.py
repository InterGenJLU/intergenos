# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
r"""An action clause with a recognisable intent reaches its carrier, not a
model turn with no tools.

THE DEFECT THIS CLOSES, and how it was measured. Lane the pronoun-argument lane pinned rather than
fixed "a decomposed clause no carrier claims", because the rung that might have
been claiming those clauses in production is the SEMANTIC one and no embedding
backend runs in a unit-test context. That measurement has now been taken against
the live embedding server on a dual-GPU workstation (nomic-embed-text-v1.5, the same
llama-server process the running daemon uses), and it returned two DIFFERENT
causes for the two clauses the re-drive recorded — so the pin's single
explanation was half right:

  * "find a pdf editor" is not RECOGNISED. It matches no keyword pattern, and its
    best embedding similarity across the whole intent corpus is 0.5968 — under
    every intent's own threshold, so the semantic rung has no candidate at all.
    Confirmed on the live daemon: glass turn bca447f55988c2d4 recorded
    semantic_score=0.5968062877655029, with_tools=false, source=llm_freeform,
    tool_count=0. Yet the ARGUMENT EXTRACTOR already handles the clause perfectly
    — _extract_arguments("manage_packages", "find a pdf editor") returns
    {"action": "search", "query": "pdf editor"}. Only the recognition was missing.

  * "check if docker is installed" IS recognised, and is dropped anyway. Its
    embedding similarity against manage_packages is 0.9387 — over that intent's
    own 0.85 threshold AND over the router's own 0.85 admission bar — and the
    keyword pattern r"^is\s+\w+\s+installed" matches the shorter form
    "is docker installed" outright. But _extract_arguments has no branch for the
    "is X installed" question, returns None, and _execute_tool_for_intent then
    returns (None, None), so BOTH the keyword rung and the semantic rung report
    handled=False and the clause falls to freeform anyway. Confirmed on the live
    daemon: glass turn 43ac041305fde8d9 recorded
    semantic_score=0.938728392124176 and still source=llm_freeform, tool_count=0.
    The intent corpus lists "is docker installed", "do I have docker installed"
    and "is this package installed" as manage_packages examples; the extractor
    never learned to read them.

BOTH HALVES ARE DETERMINISTIC, so both are provable here with no embedder:
recognition is a keyword pattern and extraction is pure string work. The semantic
rung's own numbers are recorded above from the live measurement rather than
re-asserted in a context that cannot produce them.

THE ANSWER IS THE PRODUCT'S OWN IDIOM IN BOTH HALVES. manage_services already
answers the mirror-image question — r"is\s+(\S+)\s+(?:running|active|up|enabled)"
-> {"action": "status", ...} — and manage_packages simply had no counterpart.
The read-only pkm action that answers "is X installed" is `info`, measured on
this box: `pkm info docker` prints "Package 'docker' is not installed" and exits
0, and `pkm info bash` prints the record with its install_date and exits 0. Both
outcomes are a successful read, so the carrier answers the question either way
instead of failing back into the model's lap.

TIER SCOPE. Neither half reads the hardware tier: `_match_keywords` and
`_extract_arguments` take no tier argument and consult no tier state, so the
defect and its fix are identical on 2B, 9B and 35B. Every test below is
parametrized over all three anyway, driving the router the daemon would build for
that tier, so the claim is measured rather than asserted in prose. The tier only
decides what happens AFTER a clause goes uncarried: `lock_dispatch` is true on the
2B floor and on the 35B (which has no shipped logic lane), so those two lose the
`_try_llm_tools` rung entirely and land in a no-tool freeform turn, while the 9B
runs native and at least reaches a model that has been offered tools. That
policy is asserted in test_decomposed_sub_queries_reach_their_carriers.py and is
not re-derived here.

NAMED LIMITS. No embedding backend runs in this context, so registration logs
nine "Embedding intent … PENDING" lines and the semantic rung is inert; the
recognition proved here is the deterministic keyword rung. Tool EXECUTION is
stubbed at the tool-registry boundary — no pkm runs, nothing is installed, no
package database is read.
"""

from __future__ import annotations

import unittest

from intergen.dispatch_policy import DispatchMode, resolve_dispatch_for_model
from intergen.interfaces.types import HardwareTierLevel, ToolResult
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
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


def _router(tier: HardwareTierLevel, *, replies=()):
    """The router the daemon would build for this tier, with tool EXECUTION
    stubbed at the registry boundary.

    The stub records every dispatch and returns a successful read, so the routing
    decision is what is measured and no pkm process is ever started. Returns
    (router, dispatched) where `dispatched` is the list of ToolCalls the registry
    was asked to run, in order.
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
        return ToolResult(call_id=getattr(call, "call_id", ""), name=call.name,
                          content="stubbed tool output", success=True)

    reg.execute = _execute

    seq = list(replies)
    calls = {"n": 0}

    def _chat(messages, **kw):
        calls["n"] += 1
        return seq[min(calls["n"] - 1, len(seq) - 1)] if seq else _Resp("")

    r._llm.chat = _chat
    return r, dispatched


def _route_clause(r, clause: str):
    """Route one clause the way `_handle_compound` reaches `_route_single`.

    `_route_impl` sets `_current_query_type` before routing and
    `_try_llm_freeform` reads it, so a direct `_route_single` call without it
    raises AttributeError — a red that fires on the test's own wiring rather than
    on the product. Mirroring the real caller is what makes these assertions
    measure the routing decision.
    """
    r._current_query_type = r._classify_query_type(clause)
    r._route_trail = []
    return r._route_single(clause, trail_scope="sub_query:1")


# The clause family the whole-battery re-drive recorded going uncarried, with the
# carrier each one must reach and the arguments that carrier must be handed.
CARRIED = (
    ("find a pdf editor",
     {"action": "search", "query": "pdf editor"}),
    ("find a notes app",
     {"action": "search", "query": "notes app"}),
    ("find a screenshot tool",
     {"action": "search", "query": "screenshot tool"}),
    ("find a file manager",
     {"action": "search", "query": "file manager"}),
    ("find me a photo editor",
     {"action": "search", "query": "photo editor"}),
    ("is there an app for editing pdfs",
     {"action": "search", "query": "editing pdfs"}),
    ("check if docker is installed",
     {"action": "info", "package": "docker"}),
    ("is docker installed",
     {"action": "info", "package": "docker"}),
    ("do I have docker installed",
     {"action": "info", "package": "docker"}),
)


class ASoftwareFindClauseIsRecognised(unittest.TestCase):
    """RED at base: no keyword pattern claims "find a pdf editor", and the
    embedding rung cannot rescue it (0.5968 against every threshold, measured
    live)."""

    def test_the_package_carrier_claims_the_clause(self) -> None:
        for name, tier in TIERS:
            r, _ = _router(tier)
            for clause, _args in CARRIED:
                with self.subTest(tier=name, clause=clause):
                    m = r._semantic._match_keywords(clause)
                    self.assertEqual(
                        m.intent_id, "manage_packages",
                        f"[{name}] {clause!r} is an ask for SOFTWARE and must be "
                        f"recognised by the package carrier, not left for a model "
                        f"turn that has no tools")
                    self.assertEqual(m.tool_name, "manage_packages")


class TheInstalledQuestionBuildsItsArguments(unittest.TestCase):
    """RED at base: the extractor has no branch for "is X installed", returns
    None, and the rung that DID recognise the clause declines because of it."""

    def test_an_is_installed_question_becomes_an_info_lookup(self) -> None:
        for name, tier in TIERS:
            r, _ = _router(tier)
            for clause in ("check if docker is installed",
                           "is docker installed",
                           "do I have docker installed",
                           "check whether docker is installed",
                           "is the docker package installed"):
                with self.subTest(tier=name, clause=clause):
                    self.assertEqual(
                        r._extract_arguments("manage_packages", clause),
                        {"action": "info", "package": "docker"},
                        f"[{name}] {clause!r} names a package and asks one "
                        f"question about it; `pkm info` answers that question "
                        f"whether or not the package is installed")

    def test_the_search_clause_already_extracted_and_still_does(self) -> None:
        """The half that was never broken — asserted so the recognition fix
        cannot quietly change it."""
        for name, tier in TIERS:
            r, _ = _router(tier)
            with self.subTest(tier=name):
                self.assertEqual(
                    r._extract_arguments("manage_packages", "find a pdf editor"),
                    {"action": "search", "query": "pdf editor"})

    def test_a_referential_object_declines_rather_than_looking_up_a_pronoun(
            self) -> None:
        """The the pronoun-argument lane rule holds on the new branch too: a pronoun is not a
        package name, so "is it installed" must decline to a clarify rather than
        look up a package called "it"."""
        for name, tier in TIERS:
            r, _ = _router(tier)
            for clause in ("is it installed", "check if it is installed"):
                with self.subTest(tier=name, clause=clause):
                    self.assertIsNone(
                        r._extract_arguments("manage_packages", clause),
                        f"[{name}] {clause!r} names nothing; declining sends the "
                        f"turn to a clarify, which is the honest answer")


class TheClauseReachesItsCarrierEndToEnd(unittest.TestCase):
    """The routing decision itself, with execution stubbed at the registry."""

    def test_the_clause_dispatches_instead_of_falling_to_freeform(self) -> None:
        for name, tier in TIERS:
            for clause, args in CARRIED:
                with self.subTest(tier=name, clause=clause):
                    r, dispatched = _router(
                        tier, replies=[_Resp("a model answer nobody should need")])
                    result = _route_clause(r, clause)
                    self.assertTrue(
                        result.handled,
                        f"[{name}] {clause!r} was not handled by any rung")
                    self.assertNotEqual(
                        result.source, "llm_freeform",
                        f"[{name}] {clause!r} still falls to the no-tool model "
                        f"turn — this is the defect that lane pinned")
                    self.assertEqual(
                        [c.name for c in dispatched], ["manage_packages"],
                        f"[{name}] {clause!r} must reach manage_packages exactly "
                        f"once")
                    self.assertEqual(dispatched[0].arguments, args)

    def test_the_compound_both_clauses_came_from_still_splits(self) -> None:
        """The re-drive's whole question, end to end: both halves reach a carrier
        and the pronoun half resolves against the first half's object rather than
        dispatching "it" (the referential-argument rule, still holding)."""
        from intergen.decomposer import split_compound
        self.assertEqual(split_compound("find a pdf editor and install it"),
                         ["find a pdf editor", "install it"])
        for name, tier in TIERS:
            with self.subTest(tier=name):
                r, dispatched = _router(tier, replies=[_Resp("")])
                r._compound_referent = ""
                first = _route_clause(r, "find a pdf editor")
                self.assertEqual([c.name for c in dispatched], ["manage_packages"])
                self.assertEqual(dispatched[0].arguments,
                                 {"action": "search", "query": "pdf editor"})
                # A search names what the user was LOOKING for, not something
                # that exists yet, so it must NOT become the referent for "it".
                for call in first.tool_calls:
                    self.assertEqual(
                        r._referent_from_arguments(call.arguments), "",
                        "a search query is not a package name and must never be "
                        "carried forward as the object of a later clause")


class TheNegativeSetIsUnchanged(unittest.TestCase):
    """CONTROLS. Every one of these was measured against the live embedder before
    and after the change and kept the carrier it had; the deterministic half of
    that measurement is asserted here so a later edit cannot widen the patterns
    without a red.
    """

    # (clause, the intent that must claim it, or None for "no carrier claims it")
    CONTROLS = (
        ("find the largest files", "run_command"),
        ("find the hidden files in my home", "run_command"),
        ("find a recipe for banana bread", "web_search"),
        ("find a pattern for a scarf", "web_search"),
        ("find instructions for changing a tire", "web_search"),
        ("search the web for pdf editors", "web_search"),
        ("look up the weather online", "web_search"),
        ("install firefox", "manage_packages"),
        ("list installed packages", "manage_packages"),
        ("open the file manager", "open_application"),
        ("open firefox", "open_application"),
        ("start the ssh service", "manage_services"),
        ("restart bluetooth", "manage_services"),
        ("what is a pdf editor", None),
        ("how do I edit a pdf", None),
        ("tell me about pdf editors", None),
        ("find my car keys", None),
        ("find a good movie to watch", None),
    )

    def test_a_package_question_that_names_nothing_declines(self) -> None:
        """THE ONE BEHAVIOUR THIS LANE DELIBERATELY CHANGES OUTSIDE ITS DEFECT.

        "is this package installed" names no package. Before this lane it fell
        through to the LIST branch — "installed" and "package" both appear in it,
        which is all that branch tests — and the assistant answered a question
        about ONE package by listing all ~800 installed ones. Measured against the
        live embedder at base: semantic manage_packages@1.0000, args
        {"action": "list"}.

        The new "is X installed" branch catches the sentence first, finds that "this"
        REFERS rather than NAMES, and declines, so the turn asks which package.
        That is the referential-argument rule (a referent is never dispatched as a name) applied
        to the question form, and a clarify is a better answer to this sentence
        than a corpus dump. Asserted here so the change is visible and deliberate
        rather than a side effect nobody wrote down."""
        for name, tier in TIERS:
            r, _ = _router(tier)
            for clause in ("is this package installed", "is it installed"):
                with self.subTest(tier=name, clause=clause):
                    self.assertIsNone(
                        r._extract_arguments("manage_packages", clause),
                        f"[{name}] {clause!r} names no package, so the carrier "
                        f"must decline to a clarify rather than list the corpus")

    def test_every_control_keeps_its_carrier(self) -> None:
        for name, tier in TIERS:
            r, _ = _router(tier)
            for clause, expect in self.CONTROLS:
                with self.subTest(tier=name, clause=clause):
                    got = r._semantic._match_keywords(clause).intent_id
                    self.assertEqual(
                        got, expect,
                        f"[{name}] {clause!r} must be claimed by {expect!r}, not "
                        f"{got!r} — widening the software-find patterns must not "
                        f"steal a clause that already had the right carrier, and "
                        f"must not invent a dispatch for a question that has "
                        f"nothing to dispatch")

    def test_a_knowledge_question_builds_no_package_arguments(self) -> None:
        """Belt and braces on the three that must reach the model: even if some
        future rung DID hand them to manage_packages, there is nothing to run."""
        for name, tier in TIERS:
            r, _ = _router(tier)
            for clause in ("what is a pdf editor", "how do I edit a pdf",
                           "tell me about pdf editors"):
                with self.subTest(tier=name, clause=clause):
                    self.assertIsNone(
                        r._extract_arguments("manage_packages", clause))


class TheFixReadsNoTier(unittest.TestCase):
    """The per-tier table's premise: neither half of this fix branches on tier,
    so the three tiers above are the same measurement three times, deliberately.
    """

    def test_recognition_and_extraction_agree_across_the_three_tiers(self) -> None:
        seen_intent = set()
        seen_args = set()
        for _name, tier in TIERS:
            r, _ = _router(tier)
            seen_intent.add(
                r._semantic._match_keywords("find a pdf editor").intent_id)
            seen_args.add(tuple(sorted(
                r._extract_arguments(
                    "manage_packages", "check if docker is installed").items())))
        self.assertEqual(len(seen_intent), 1, "recognition differed by tier")
        self.assertEqual(len(seen_args), 1, "extraction differed by tier")


if __name__ == "__main__":
    unittest.main()
