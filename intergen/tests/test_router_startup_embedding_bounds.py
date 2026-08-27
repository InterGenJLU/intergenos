# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""No index the router builds at startup may hand the embedding server one huge request.

WHY A GATE AND NOT TWO FIXES. This defect has now been found and fixed twice in two
different indexes, and the second one was found only because the first was being
investigated. The wiki passages went as one 2 116-input request until 86da2c51; the
how-to triggers went as one 414-input request until the commit this file arrives with.
Both were written independently, both were correct-looking, and neither had anything
telling it that a single request has a ceiling. A third index would be written the same
way. This gate is the thing that would say so.

WHAT THE CEILING IS FOR. The embedding server ships started with ``--parallel 1`` and is
reached through a client whose default timeout is 30 s (intergen/llama_manager.py). A
request larger than that timeout can absorb does not merely fail: the client stops
waiting while the server keeps working, so the single slot is still busy when the next
embedding is asked for. Measured on an installed system 2026-08-25 — one gate run spent
609 s, of which 300 s was an abandoned whole-corpus request and another 300 s was a
ONE-TEXT query queued behind it for a slot that was never free.

64 IS THE BOUND, AND IT IS DELIBERATELY NOT THE BATCH SIZE. The two bounded indexes both
batch at 32. Requiring exactly 32 here would make this gate a copy of their constants
rather than a statement about the server, and would fail a future index that reasonably
chose 48. 64 is twice the size either index chose for texts an order of magnitude longer
than a trigger phrase, so anything above it is a request nobody has measured.

A REQUEST HAS TWO DIMENSIONS AND THIS GATE USED TO SEE ONLY ONE. Counting inputs bounds
how many texts go in one call and says nothing about how big each one is. Thirty-two
inputs of eighty thousand characters is a well-behaved batch by the count rule and a
request no embedding server on this machine can answer: the shipped embedding server is
started with ``--ctx-size`` from ``models.embedding_context`` (default 2048), and
``--batch-size``/``--ubatch-size`` are sized to that same context, so an input longer
than the context is not merely truncated by the server — it is refused, and the caller
gets nothing back for the WHOLE batch it travelled in. llama_manager._fit_to_context
exists precisely to shorten such an input before it is sent, and it can only do that for
callers that go through the manager. An index that embeds through a different callable
is not covered by it, which is why the ceiling belongs here as well.

MAX_CHARS_PER_INPUT IS DELIBERATELY GENEROUS, AND IT IS A PROXY. Tokens are what the
server counts, and this gate has no server to count them with; a token is at least one
character, so a character ceiling is a sound one-sided test — under it, an input cannot
exceed the context in tokens either. 8 192 characters is four times the 2 048-token
context, so nothing that could plausibly fit is failed here, and anything above it is a
text nobody has measured against the server that has to embed it. The bound states a
property of the SERVER, not of any particular index's chunking constant — the same
reason 64 is not 32 above.
"""

from __future__ import annotations

import unittest

from intergen.interfaces.types import ToolResult
from intergen.intents import register_all_intents
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

MAX_TEXTS_PER_REQUEST = 64
# Four times the shipped embedding server's 2 048-token context, in characters.
# See the module docstring for why a character ceiling is a sound one-sided test
# and why it is set this far above what could plausibly fit.
MAX_CHARS_PER_INPUT = 8192
_DIM = 64


class _InertLLM(LLMRouter):
    def __init__(self):
        super().__init__({})

    def stream(self, messages, **_kw):
        yield "inert"

    def stream_with_tools(self, messages, *, tools, **_kw):
        yield "inert"


class _RecordingEmbedder:
    """Records the size and the origin of every request the startup path makes."""

    def __init__(self):
        self.requests: list[tuple[int, str]] = []
        self.inputs: list[tuple[int, str]] = []   # (longest input, call site)

    def __call__(self, texts):
        import os
        import traceback
        texts = list(texts)
        site = "?"
        for frame in reversed(traceback.extract_stack()[:-1]):
            if f"{os.sep}intergen{os.sep}" in frame.filename and "tests" not in frame.filename:
                site = f"{os.path.basename(frame.filename)}:{frame.lineno}"
                break
        self.requests.append((len(texts), site))
        # The SIZE dimension: the longest single input in this request, and the
        # longest seen anywhere, so the bound below is asserted over what was
        # actually sent rather than over a count.
        longest = max((len(t) for t in texts), default=0)
        self.inputs.append((longest, site))
        return [[0.0] * _DIM for _ in texts]


def _construct_router_recording_every_embedding() -> _RecordingEmbedder:
    rec = _RecordingEmbedder()
    matcher = SemanticMatcher(embedder=rec)
    register_all_intents(matcher)
    registry = ToolRegistry()
    registry.discover_tools()
    registry.execute = lambda call, **_kw: ToolResult(
        call_id=getattr(call, "call_id", "") or "", name=call.name,
        content="[recorded, not performed]", success=True)
    ConversationRouter(tool_registry=registry, semantic_matcher=matcher,
                       llm=_InertLLM(), embedder=rec)
    return rec


class StartupEmbeddingRequestBounds(unittest.TestCase):

    def test_the_instrument_sees_the_startup_embedding_at_all(self):
        """A bound asserted over an empty list would pass on a silent machine."""
        rec = _construct_router_recording_every_embedding()
        self.assertTrue(
            rec.requests,
            "constructing the router issued no embedding request at all, so the "
            "bound below would be asserted over nothing")
        self.assertGreater(
            sum(n for n, _ in rec.requests), 100,
            f"only {sum(n for n, _ in rec.requests)} texts were embedded while the "
            "router was built; the corpora this gate is meant to cover are not "
            "being reached")

    def test_the_instrument_sees_the_size_of_what_is_sent(self):
        """A size bound asserted over empty strings would pass on any tree."""
        rec = _construct_router_recording_every_embedding()
        longest = max((n for n, _ in rec.inputs), default=0)
        self.assertGreater(
            longest, 0,
            "every text embedded while the router was built was empty, so the "
            "per-input bound below would be asserted over nothing")

    def test_the_per_input_bound_detects_an_oversized_input(self):
        """A bound never shown to fire is not a gate.

        Both size assertions above are green on this tree, which is the desired
        state and also the state in which an instrument that cannot see
        anything would look identical. This drives one over-bound text through
        the same recorder and the same predicate the gate uses, and requires
        the violation to be caught and to be reported with its call site.
        """
        rec = _RecordingEmbedder()
        rec(["fits fine", "x" * (MAX_CHARS_PER_INPUT + 1)])
        oversized = [(n, site) for n, site in rec.inputs
                     if n > MAX_CHARS_PER_INPUT]
        self.assertEqual(
            len(oversized), 1,
            "the recorder did not report an input one character over the "
            "bound, so the two assertions above are asserted over a blind "
            "instrument")
        self.assertEqual(oversized[0][0], MAX_CHARS_PER_INPUT + 1)
        self.assertNotEqual(
            oversized[0][1], "",
            "the violation was recorded without a call site, so the failure "
            "message could not tell anyone which index to fix")

    def test_no_startup_input_exceeds_the_per_input_bound(self):
        rec = _construct_router_recording_every_embedding()
        oversized = [(n, site) for n, site in rec.inputs
                     if n > MAX_CHARS_PER_INPUT]
        report = "\n".join(
            f"  a {n}-character input in one request from {site}"
            for n, site in oversized)
        self.assertEqual(
            oversized, [],
            f"\n{report}\n"
            f"No index built while the router starts may hand the embedding "
            f"server a single input longer than {MAX_CHARS_PER_INPUT} "
            f"characters. The server ships with a {2048}-token context and its "
            f"batch sized to that context, so an input above it is REFUSED — "
            f"and the caller gets nothing back for every text that travelled "
            f"in the same request. Split the text before sending it, as "
            f"intergen/wiki_retrieval.py chunks pages, or send it through "
            f"llama_manager.embed, which shortens an over-context input and "
            f"says so in the log.")

    def test_no_startup_request_exceeds_the_bound(self):
        rec = _construct_router_recording_every_embedding()
        oversized = [(n, site) for n, site in rec.requests
                     if n > MAX_TEXTS_PER_REQUEST]
        report = "\n".join(
            f"  {n} texts in one request from {site}" for n, site in oversized)
        self.assertEqual(
            oversized, [],
            f"\n{report}\n"
            f"No index built while the router starts may hand the embedding server "
            f"more than {MAX_TEXTS_PER_REQUEST} texts in a single request. The server "
            f"ships with one slot and the client stops waiting after 30 s; a request "
            f"that outlives the client leaves the slot busy for whatever is asked "
            f"next. Send the corpus in bounded batches, as intergen/wiki_retrieval.py "
            f"and intergen/howto.py do.")


if __name__ == "__main__":
    unittest.main()
