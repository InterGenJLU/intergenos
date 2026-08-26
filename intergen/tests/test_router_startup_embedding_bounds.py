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

THIS GATE RUNS WITHOUT A BACKEND. The embedder is a recording stand-in — it stands in
for the NETWORK, which is the only thing it replaces; the matcher, the registry, the
corpora and the router are the shipped ones.
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
