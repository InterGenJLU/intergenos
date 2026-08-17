# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""RED/GREEN tests for the defensive vision gate (llm.py).

An image_data-bearing turn reaching a model whose has_vision is False gets a
code-owned honest reply + a glass marker — never a silent swallow, never image
parts sent to a non-vision backbone. Both shipped tiers (2B + 9B) declare
has_vision, so the gate is defensive dead code in practice; these tests pin it
so a future vision-dark model can never silently drop an image.
"""
from __future__ import annotations

import unittest
from unittest import mock

from intergen import glass
from intergen.llm import LLMRouter
from intergen.interfaces.types import Message, MessageRole

_IMG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"  # stub data URI


def _msgs():
    return [Message(role=MessageRole.USER, content="what's in this image?")]


class VisionGateTests(unittest.TestCase):
    # ── RED: no-vision model + image → honest reply, model never contacted ────
    def test_stream_blocks_image_on_non_vision_model(self):
        r = LLMRouter({"has_vision": False})
        with mock.patch("urllib.request.urlopen") as urlopen:
            out = list(r.stream(_msgs(), image_data=_IMG))
        self.assertEqual(out, [LLMRouter._VISION_UNSUPPORTED_MSG])
        urlopen.assert_not_called()

    def test_stream_with_tools_blocks_image_on_non_vision_model(self):
        r = LLMRouter({"has_vision": False, "tool_calling": True})
        with mock.patch("urllib.request.urlopen") as urlopen:
            out = list(r.stream_with_tools(_msgs(), tools=[], image_data=_IMG))
        self.assertEqual(out, [LLMRouter._VISION_UNSUPPORTED_MSG])
        urlopen.assert_not_called()

    def test_gate_emits_glass_marker(self):
        r = LLMRouter({"has_vision": False})
        with mock.patch.object(glass, "emit") as emit, \
                mock.patch("urllib.request.urlopen"):
            list(r.stream(_msgs(), image_data=_IMG))
        events = [c.args[:2] for c in emit.call_args_list]
        self.assertIn(("model", "vision_unsupported"), events)

    # ── GREEN: vision model + image → gate passes through to the model ────────
    def test_stream_passes_image_on_vision_model(self):
        r = LLMRouter({"has_vision": True})
        # urlopen raises → stream() catches + returns []; the point is the gate
        # did NOT short-circuit with the honest message (it proceeded past it).
        with mock.patch("urllib.request.urlopen",
                        side_effect=OSError("no server")):
            out = list(r.stream(_msgs(), image_data=_IMG))
        self.assertNotIn(LLMRouter._VISION_UNSUPPORTED_MSG, out)

    # ── GREEN: no image → gate never fires, regardless of capability ──────────
    def test_no_image_never_triggers_gate(self):
        r = LLMRouter({"has_vision": False})
        with mock.patch("urllib.request.urlopen",
                        side_effect=OSError("no server")):
            out = list(r.stream(_msgs()))
        self.assertNotIn(LLMRouter._VISION_UNSUPPORTED_MSG, out)

    # ── fail-closed default: an unset capability is treated as no-vision ──────
    def test_default_has_vision_is_false_fail_closed(self):
        self.assertFalse(LLMRouter({})._has_vision)


if __name__ == "__main__":
    unittest.main()
