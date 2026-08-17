# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The tool-call refusal's advice must be advice the gate itself accepts.

The defect this pins: the literal-marker refusal in
:func:`corpus_to_sft.assert_renderable_tool_calls` told an author that a gold
needing to QUOTE tool-call syntax could carry it "in a prose gold about the
format" — but a prose gold's content becomes an ASSISTANT message
(:func:`corpus_to_sft.render_gold_message`), which is exactly the position the
refusal fires on. An author who followed the printed advice hit the identical
refusal again. Nothing asserted the message's wording, so the suite could not
see the contradiction.

These tests bind the advice to the gate's behaviour: every carrier the message
names is exercised through the real gate and must pass, the prose-gold route
must refuse, and the message must state the prose-gold impossibility rather
than offer it as an escape.
"""

from __future__ import annotations

import unittest

from intergen.tests import corpus_to_sft
from intergen.tests.corpus_to_sft import (CorpusError,
                                          assert_renderable_tool_calls,
                                          render_gold_message)

_MARKER = corpus_to_sft._LITERAL_CALL_MARKER


def _sample(messages):
    return {"messages": messages}


class RefusalEscapeRouteTests(unittest.TestCase):
    """The refusal message and the gate agree about where the marker may live."""

    def test_user_turn_carries_the_marker(self):
        assert_renderable_tool_calls(_sample([
            {"role": "user", "content": f"what does {_MARKER} mean?"},
            {"role": "assistant", "content": "an explanation"}]),
            locator="pin")

    def test_tool_result_carries_the_marker(self):
        assert_renderable_tool_calls(_sample([
            {"role": "tool", "content": f"output holds {_MARKER}"},
            {"role": "assistant", "content": "a synthesis"}]),
            locator="pin")

    def test_prose_gold_with_the_marker_is_refused(self):
        gold_msg = render_gold_message(
            {"content": f"The format looks like {_MARKER} in a rendered row."})
        with self.assertRaises(CorpusError):
            assert_renderable_tool_calls(
                _sample([{"role": "user", "content": "q"}, gold_msg]),
                locator="pin")

    def test_the_routes_hold_on_the_author_path_too(self):
        """The same three routes through entry_to_sample — the path a corpus
        author actually travels, where the gate fires on every sample."""
        def entry(user_text, gold_content):
            return {"id": "pin-entry", "category": "pin", "intent": "pin",
                    "training_provenance": "pin",
                    "turns": [{"user": user_text,
                               "gold": {"content": gold_content}}]}
        corpus_to_sft.entry_to_sample(
            entry(f"what does {_MARKER} mean?", "an explanation"),
            system_prompt="s")
        with self.assertRaises(CorpusError):
            corpus_to_sft.entry_to_sample(
                entry("q", f"The format looks like {_MARKER}."),
                system_prompt="s")

    def test_the_message_offers_only_routes_the_gate_accepts(self):
        """The wording itself, so advice and gate cannot drift apart again."""
        with self.assertRaises(CorpusError) as ctx:
            assert_renderable_tool_calls(_sample([
                {"role": "assistant", "content": f"literal {_MARKER} here"}]),
                locator="pin")
        message = str(ctx.exception)
        self.assertIn("user turn", message)
        self.assertIn("tool result", message)
        self.assertIn(
            "A prose gold cannot", message,
            "the refusal must state that a prose gold cannot carry the "
            "marker — its prose becomes assistant content, the refused "
            "position — instead of offering it as an escape route")


if __name__ == "__main__":
    unittest.main()
