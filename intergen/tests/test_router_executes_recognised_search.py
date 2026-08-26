# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An explicitly recognised web search is EXECUTED, never described or offered.

WHERE THIS COMES FROM. The first person outside the project to use the assistant asked
it to search the web four times, in three different word orders, across two sessions.
She was never given a search. Two router gates decided those turns before any dispatch
path saw them: the web capability gate answered "yes, I can search the web", and the
current-data offer answered "want me to look it up?" — and neither staged anything for
the "yes" she then typed. The intent matcher recognised every one of those sentences as
web_search; it was simply never consulted, because both gates run ahead of it.

A CORRECTED MEASUREMENT MADE THIS THE WORK. An earlier gate reported the matcher at
19/19 on those sentences while the product still served none of the five. That gate was
right about the layer it measured and silent about where the decision was really made.
Driving the real router over the same nineteen sentences with the real tool registry,
ten were served before this change and fifteen after it.

WHAT IS PINNED HERE, AND WHY BOTH HALVES ARE PINNED TOGETHER. The condition for stepping
aside is recognition AND a named target — never recognition alone. Against the shipped
corpus the matcher resolves BARE CAPABILITY QUESTIONS to web_search too: "can you search
the web?" takes the same web_search keyword as "web search for the average price of X".
Bypassing the capability gate on recognition alone would answer "can you search the
web?" by searching the web for the words "search the web" — an honest, grounded answer
replaced by a nonsense dispatch. The two groups in this file are therefore one test in
two parts: same keyword verdict, opposite routing outcome.

THE EMBEDDING LAYER IS ABSENT HERE, DELIBERATELY, AND THAT IS STATED RATHER THAN HIDDEN.
This gate runs in the ordinary suite on any machine, so it builds the matcher with no
embedding backend; ``_match_embeddings`` then returns nothing and every verdict below is
the KEYWORD layer's. That is not a weakened version of the real path — the five field
sentences and three of the capability questions are keyword-decided on the real system
too, which is exactly what makes them the pair that has to be told apart. The sentences
that need the embedding layer to be recognised at all are covered by the installed-tier
gate beside the 149 classification gate, which fails rather than skips when the real
embedding server is not answering.

THE TARGET-EXTRACTION TABLE LIVES IN ITS OWN FILE, AND NOT FOR TIDINESS. It was here
first, and importing the new helper made this whole module fail to COLLECT against the
unmodified tree — which proves only that a new function is absent, not that the old
behaviour was wrong. A red-first gate has to fail on BEHAVIOUR at the base it is written
against, so nothing in this file imports anything the base does not already have. The
table is in test_web_search_target_extraction.py.

NO TOOL IS EXECUTED BY THIS FILE. ``ToolRegistry.execute`` is replaced with a recording
stand-in. This is not tidiness: the shipped router RUNS the tool on the way to building
its answer, so a test that drove the router without this would perform real web searches
from the machine running the suite.
"""

from __future__ import annotations

import threading
import unittest

from intergen.interfaces.types import ToolResult
from intergen.intents import register_all_intents
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry


# The five sentences the router left unserved, verbatim from the sealed field trace.
FIELD_DISPATCHES = [
    "Can you web search and see how much a chippendale dining table sells for?",
    "Can you websearch to find that picture for me?",
    "web search for the average price of a trilogy mill hill bead kit",
    "do a web search for the average price of a trilogy mill hill bead kit",
    "yes, do a web search for the average price of a trilogy mill hill bead kit",
]

# Capability questions the KEYWORD layer also resolves to web_search. These are the
# sentences that break if recognition alone is the condition, which is why they are
# here rather than in a separate file.
BARE_CAPABILITY_QUESTIONS = [
    "can you search the web?",
    "can you web search?",
    "can you search the internet?",
]

# Capability questions the KEYWORD layer does NOT take. These are the only sentences
# that can show the embedding cost, because a keyword-decided sentence never reaches the
# embedding layer whatever the order is — a first version of the cost guard was built
# from the list above and a mutation that put the matcher first passed it clean.
KEYWORD_MISSING_CAPABILITY_QUESTIONS = [
    "can you go online?",
    "are you able to search the web",
    "do you have internet access?",
    "do you browse the internet?",
    "are you connected to the internet",
]


class _InertLLM(LLMRouter):
    """The SHIPPED model client with only its two network entry points replaced.

    THE FIRST VERSION OF THIS WAS A HAND-BUILT OBJECT AND IT WAS THE WRONG SHAPE. It
    grew an attribute at a time as the router asked for the next real thing —
    ``_SYNTHESIS_RULES``, then ``_strip_filler``, then ``_gate_reason`` — and each
    addition was a guess at what the shipped class does. Subclassing removes the whole
    failure mode: the quality gate, the filler strip, the semantic-health screen and the
    system-message construction below are the shipped code, and only ``stream`` (which
    opens the socket) is replaced.

    ``stream`` ANSWERS. That is not laxity: once the router has dispatched web_search it
    calls the model to phrase the tool's output, and a stand-in that refused would fail
    the very turns this gate wants to see succeed. What must never happen on this path
    is the model DECIDING a tool, so ``stream_with_tools`` fails loudly instead — the
    shipped locked floor never offers tools to the model, and a turn that got there
    would mean the deterministic layers had already let the sentence go.
    """

    INERT = ("Here is what the search returned, summarised from the tool output above "
             "so the answer stays grounded in what was actually retrieved.")

    def __init__(self) -> None:
        super().__init__({})
        self.synthesis_calls = 0

    def stream(self, messages, **_kw):
        self.synthesis_calls += 1
        yield self.INERT

    def stream_with_tools(self, messages, *, tools, **_kw):
        raise AssertionError(
            "The model was offered tools. On the shipped locked floor the model never "
            "decides a tool, so a turn reaching here means the deterministic layers "
            "let the sentence go — the property this gate exists for.")


def _build_router(*, with_web_search: bool = True):
    """The real matcher, the real registry and the real router — no model, no actions."""
    matcher = SemanticMatcher(embedder=None)
    register_all_intents(matcher)
    registry = ToolRegistry()
    registry.discover_tools()
    if not with_web_search:
        # Model a machine without the tool by REMOVING the tool, so the router asks its
        # own question ("is web_search registered?") and gets a real answer.
        for attr in ("_tools", "_registry", "tools"):
            store = getattr(registry, attr, None)
            if isinstance(store, dict) and "web_search" in store:
                del store["web_search"]
                break
    executed: list[str] = []

    def recording_execute(call, **_kw):
        executed.append(call.name)
        return ToolResult(
            call_id=getattr(call, "call_id", "") or "",
            name=call.name,
            content="[tool execution recorded, not performed]",
            success=True,
        )

    registry.execute = recording_execute
    router = ConversationRouter(tool_registry=registry, semantic_matcher=matcher,
                                llm=_InertLLM(), embedder=None)
    return router, registry, executed


class RecognisedSearchIsExecuted(unittest.TestCase):
    """The five field sentences reach web_search through the shipped router."""

    def test_every_field_search_sentence_dispatches_web_search(self):
        router, registry, executed = _build_router()
        self.assertIn("web_search", registry.get_all_names(),
                      "web_search must be registered or this gate proves nothing")
        wrong = []
        for sentence in FIELD_DISPATCHES:
            router.reset_conversation_state()
            before = len(executed)
            result = router.route(sentence)
            staged = [c.name for c in getattr(result, "tool_calls", [])]
            if "web_search" not in staged or executed[before:] != ["web_search"]:
                wrong.append((sentence, getattr(result, "source", None), staged,
                              executed[before:]))
        self.assertFalse(wrong, "\n".join(
            [""] + [f"  NOT DISPATCHED source={src!r} staged={staged} executed={ex}"
                    f"\n    :: {s}" for s, src, staged, ex in wrong]
            + ["", "Each line is a turn a real first-time user typed and did not get "
               "served. The router recognised the sentence and answered about itself "
               "instead of doing what was asked."]))

    def test_the_matcher_recognises_the_capability_questions_the_same_way(self):
        """The premise this file rests on, asserted instead of assumed.

        If the matcher ever stops resolving the bare capability questions to
        web_search, the discrimination test below still passes while no longer testing
        anything — the two groups would be told apart by the matcher rather than by the
        target rule. Pinning the premise keeps that from happening silently.
        """
        matcher = SemanticMatcher(embedder=None)
        register_all_intents(matcher)
        for sentence in BARE_CAPABILITY_QUESTIONS + FIELD_DISPATCHES:
            with self.subTest(sentence=sentence):
                match = matcher._match_keywords(matcher._normalize_input(sentence))
                self.assertEqual(
                    match.tool_name, "web_search",
                    f"{sentence!r} is no longer resolved to web_search by the keyword "
                    f"layer, so this file's two groups are no longer the matched pair "
                    f"it claims to test.")

    def test_a_bare_capability_question_is_still_answered_not_searched(self):
        router, _registry, executed = _build_router()
        wrong = []
        for sentence in BARE_CAPABILITY_QUESTIONS:
            router.reset_conversation_state()
            before = len(executed)
            result = router.route(sentence)
            staged = [c.name for c in getattr(result, "tool_calls", [])]
            if (getattr(result, "source", None) != "capability_question"
                    or staged or executed[before:]):
                wrong.append((sentence, getattr(result, "source", None), staged))
        self.assertFalse(wrong, "\n".join(
            [""] + [f"  source={src!r} staged={staged}\n    :: {s}"
                    for s, src, staged in wrong]
            + ["", "A question about what the assistant CAN do was turned into a search "
               "for its own wording. The capability answer is grounded and true; a "
               "search for the words 'search the web' is neither."]))

    def test_a_capability_question_never_pays_for_an_embedding(self):
        """The cost guard, and it is not a micro-optimisation.

        The gate that steps aside for a recognised dispatch has to decide something
        about every capability question the router answers. Deciding it by asking the
        matcher first would put an embedding call in front of a turn that answers today
        from a regex and the tool registry. MEASURED on a machine of the kind this tier
        is built for — the embedding server on the CPU, no GPU layers — ONE embedding of
        one sentence takes 57 seconds. A capability question that used to answer in
        about a millisecond would have started taking a minute.

        The condition that rules most sentences out is pure string work, so it runs
        first and the matcher is never reached for a sentence that names nothing. This
        counts the embedder calls to prove it, because the ordering is invisible in the
        result and would come back the moment someone tidied the function.
        """
        # THE WINDOW MATTERS AND THE FIRST VERSION OF THIS TEST GOT IT WRONG. Building
        # a router embeds two corpora — the teaching how-tos and the wiki retrieval
        # index — so counting from construction counts fourteen calls that have nothing
        # to do with routing. The count that matters is the one taken across route()
        # alone, so the counter is armed after the router exists.
        embed_calls: list[list[str]] = []
        routing = False
        this_thread = threading.get_ident()

        def counting_embedder(texts):
            # ONLY THIS THREAD COUNTS, AND THE REASON IS MEASURED. Every finished turn
            # is embedded by the conversational memory on a BACKGROUND thread
            # (memory.py's drain), so a naive count records one call per turn that has
            # nothing to do with deciding the route and happens identically without this
            # change. Routing runs on the calling thread; that is the window under test.
            if routing and threading.get_ident() == this_thread:
                embed_calls.append(list(texts))
            # A plausible vector, so construction succeeds and the matcher registers its
            # intents on the embedding layer exactly as it does in production. The point
            # of this test is WHETHER the layer is consulted while answering, not what
            # it would have said.
            return [[0.0] * 768 for _ in texts]

        matcher = SemanticMatcher(embedder=counting_embedder)
        register_all_intents(matcher)
        registry = ToolRegistry()
        registry.discover_tools()
        registry.execute = lambda call, **_kw: None
        router = ConversationRouter(tool_registry=registry, semantic_matcher=matcher,
                                    llm=_InertLLM(), embedder=counting_embedder)
        routing = True
        for sentence in (BARE_CAPABILITY_QUESTIONS
                         + KEYWORD_MISSING_CAPABILITY_QUESTIONS):
            with self.subTest(sentence=sentence):
                router.reset_conversation_state()
                result = router.route(sentence)
                self.assertEqual(getattr(result, "source", None), "capability_question")
        self.assertEqual(
            embed_calls, [],
            "The embedding layer was consulted while answering a capability question. "
            "On the hardware this tier targets that is a 57-second answer to a question "
            "that used to take a millisecond: the free string test must run before the "
            "matcher is asked anything.")

    def test_no_dispatch_when_the_tool_is_not_registered(self):
        """An honest 'not available' is never traded for a dispatch that cannot run.

        On a machine without web search the capability gate's answer is TRUE. Routing
        on from it because the sentence looks like a dispatch would leave the person
        with nothing instead of with the truth.
        """
        router, registry, executed = _build_router(with_web_search=False)
        self.assertNotIn("web_search", registry.get_all_names())
        for sentence in FIELD_DISPATCHES[:2] + BARE_CAPABILITY_QUESTIONS[:1]:
            with self.subTest(sentence=sentence):
                router.reset_conversation_state()
                before = len(executed)
                result = router.route(sentence)
                staged = [c.name for c in getattr(result, "tool_calls", [])]
                self.assertEqual(staged, [], f"{sentence!r} staged {staged}")
                self.assertEqual(executed[before:], [],
                                 f"{sentence!r} executed a tool that is not registered")
                self.assertIn("available", (getattr(result, "text", "") or "").lower(),
                              f"{sentence!r} did not say the capability is absent")


if __name__ == "__main__":
    unittest.main()
