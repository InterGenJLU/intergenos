# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""AI-12 part A — SemanticMatcher embeddings via an injected llama.cpp embedder.

Confirms Layer-2 matching works through the injected embedder (replacing the
in-process sentence-transformers/torch/huggingface stack) AND degrades
gracefully — no embedder / embedding server down -> Layer-2 skipped, never a
crash, so keyword + LLM layers still serve.
"""

from __future__ import annotations

import unittest

from intergen.semantic import SemanticMatcher


def _fake_embedder(texts):
    """Deterministic, separable 4-d vectors: 'hello*' near 'hello*', far from 'bye*'."""
    out = []
    for t in texts:
        if t.lower().startswith("hello") or "greet" in t.lower():
            out.append([1.0, 0.0, 0.0, 0.0])
        else:
            out.append([0.0, 1.0, 0.0, 0.0])
    return out


class EmbedderInjectionTests(unittest.TestCase):
    def test_matches_via_injected_embedder(self):
        m = SemanticMatcher(embedder=_fake_embedder)
        m.register_intent("greet", ["hello there"], threshold=0.8, tool_name="greet_tool")
        r = m._match_embeddings("hello friend")
        self.assertEqual(r.intent_id, "greet")
        self.assertEqual(r.tool_name, "greet_tool")
        self.assertGreaterEqual(r.score, 0.8)

    def test_below_threshold_no_match(self):
        m = SemanticMatcher(embedder=_fake_embedder)
        m.register_intent("greet", ["hello there"], threshold=0.8)
        r = m._match_embeddings("goodbye now")  # orthogonal vector -> low sim
        self.assertIsNone(r.intent_id)

    def test_no_embedder_skips_registration(self):
        # No embedding backend: register_intent skips (warns) rather than crashes.
        m = SemanticMatcher(embedder=None)
        m.register_intent("greet", ["hello there"])
        self.assertEqual(m.get_intent_count(), 0)

    def test_no_embedder_match_degrades(self):
        m = SemanticMatcher(embedder=None)
        r = m._match_embeddings("hello there")
        self.assertIsNone(r.intent_id)
        self.assertEqual(r.layer, "embedding")

    def test_embedder_returning_none_degrades(self):
        # Embedder present but server unavailable (returns None): no crash.
        m = SemanticMatcher(embedder=lambda texts: None)
        m.register_intent("greet", ["hello there"])  # skipped
        self.assertEqual(m.get_intent_count(), 0)
        self.assertIsNone(m._match_embeddings("hello there").intent_id)

    def test_keyword_layer_unaffected_without_embedder(self):
        # Layer 1 keyword matching must still work with no embedding backend.
        m = SemanticMatcher(embedder=None)
        m.register_keyword_pattern("disk", [r"disk\s+usage"], tool_name="run_command")
        r = m.match("show disk usage")
        self.assertEqual(r.intent_id, "disk")
        self.assertEqual(r.layer, "keyword")

    def test_ragged_embedding_shape_degrades_not_crashes(self):
        # Security-lens part-A catch (WC's part-C class, one layer up): a ragged or
        # non-numeric embedder return makes np.asarray(float32) raise — _embed must
        # degrade to None, not propagate the ValueError to the caller.
        ragged = SemanticMatcher(embedder=lambda texts: [[1.0, 2.0], [3.0]])
        self.assertIsNone(ragged._embed(["a", "b"]))
        nonnumeric = SemanticMatcher(embedder=lambda texts: [["a", "b"]])
        self.assertIsNone(nonnumeric._embed(["q"]))
        # And the match path degrades rather than crashing.
        self.assertIsNone(ragged._match_embeddings("hello there").intent_id)


if __name__ == "__main__":
    unittest.main()
