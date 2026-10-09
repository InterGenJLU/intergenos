# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The web page's /clear reaches the server, and the pane follows the server.

The server's /clear empties the model's buffer and withdraws every offer staged
in it, so a bare "yes" typed afterwards takes up nothing. The page's /clear
emptied only its own pane and sent nothing: the person saw a cleared
conversation while the model still read the old one, and a "yes" still reached
the offer the cleared conversation held.

The page now sends the command, and empties its pane when the server says the
buffer is empty (buffer_cleared), so the pane and the buffer cannot disagree.
With no open connection nothing can be cleared, and the page says so instead
of emptying the pane.

The page is run whole under node (web_page_harness), so these cases exercise
the shipped app.js.
"""

from __future__ import annotations

import unittest

from intergen.tests.web_page_harness import (
    NEEDS_NODE, NODE, connected, pane_texts, run_page,
)

_A_TURN = [
    {"type": "what time is it in Tokyo"},
    {"server": {"type": "turn_ack", "turn_id": "t1"}},
    {"server": {"type": "stream_start", "turn_id": "t1"}},
    {"server": {"type": "stream_token", "turn_id": "t1",
                "token": "It is evening there."}},
    {"server": {"type": "stream_end", "turn_id": "t1",
                "full_response": "It is evening there."}},
]


@unittest.skipUnless(NODE, NEEDS_NODE)
class TheWebPagesClearReachesTheServer(unittest.TestCase):

    def test_clear_sends_the_command_to_the_server(self) -> None:
        record = run_page(connected() + _A_TURN + [{"type": "/clear"}])
        self.assertIn(
            {"type": "slash_command", "command": "/clear"}, record["sent"],
            "the page's /clear sent nothing to the server, so the model's "
            "buffer and the offers staged in it outlived the cleared pane; "
            f"frames sent: {record['sent']}")

    def test_the_pane_is_emptied_when_the_server_says_the_buffer_is(
            self) -> None:
        record = run_page(connected() + _A_TURN + [
            {"type": "/clear"},
            {"mark": "before the server answered"},
            {"server": {"type": "buffer_cleared"}},
        ])
        before = record["marks"][0]["pane"]
        self.assertEqual(
            pane_texts(before),
            ["what time is it in Tokyo", "It is evening there."],
            "the pane was emptied before the server had emptied the buffer "
            "it shows")
        self.assertEqual(record["pane"], [],
                         "buffer_cleared did not empty the pane")

    def test_clear_without_a_connection_says_so_and_clears_nothing(
            self) -> None:
        record = run_page(connected() + _A_TURN + [
            {"close_from_server": True},
            {"type": "/clear"},
        ])
        self.assertEqual(record["sent"][-1]["type"], "message",
                         "a frame was sent on a closed socket")
        self.assertEqual(
            pane_texts(record["pane"]),
            ["what time is it in Tokyo", "It is evening there."],
            "the pane was emptied although nothing reached the server")
        self.assertTrue(
            any("Not connected" in b["text"] for b in record["banners"]),
            f"the person was not told: banners {record['banners']}")



@unittest.skipUnless(NODE, NEEDS_NODE)
class AClearedTurnsLateFramesAreNotShown(unittest.TestCase):
    """/clear during a turn: the page emptied its pane on buffer_cleared and
    then put the old turn's answer into it when it arrived. The page now ends
    the turn on buffer_cleared — the server has ended it — and does not show a
    frame that belongs to it. The server no longer sends one (it ends the turn
    before it acknowledges the clear); the page holds the same line for a frame
    already on its way."""

    _CLEAR_DURING_A_TURN = [
        {"type": "old question"},
        {"server": {"type": "turn_ack", "turn_id": "old-turn"}},
        {"type": "/clear"},
        {"server": {"type": "buffer_cleared"}},
    ]

    def test_the_old_turns_answer_is_not_put_into_the_cleared_pane(self) -> None:
        record = run_page(connected() + self._CLEAR_DURING_A_TURN + [
            {"server": {"type": "response", "turn_id": "old-turn",
                        "content": "answer to the cleared question",
                        "source": "keyword"}},
            {"wait": 1000},
            {"inspect": "#thinking-indicator"},
        ], constants={"POST_ACK_TIMEOUT_MS": 80})
        self.assertEqual(pane_texts(record["pane"]), [])
        self.assertIn("hidden", record["inspected"][0]["classes"],
                      "the thinking indicator stayed up for a turn that ended")

    def test_a_streamed_answer_of_the_old_turn_is_not_shown(self) -> None:
        record = run_page(connected() + self._CLEAR_DURING_A_TURN + [
            {"server": {"type": "stream_start", "turn_id": "old-turn"}},
            {"server": {"type": "stream_token", "turn_id": "old-turn",
                        "token": "answer to the cleared question"}},
            {"server": {"type": "stream_end", "turn_id": "old-turn",
                        "full_response": "answer to the cleared question"}},
        ], constants={"POST_ACK_TIMEOUT_MS": 80})
        self.assertEqual(pane_texts(record["pane"]), [])

    def test_control_the_next_turn_is_shown(self) -> None:
        record = run_page(connected() + self._CLEAR_DURING_A_TURN + [
            {"type": "new question"},
            {"server": {"type": "turn_ack", "turn_id": "new-turn"}},
            {"server": {"type": "stream_start", "turn_id": "new-turn"}},
            {"server": {"type": "stream_token", "turn_id": "new-turn",
                        "token": "answer to the new question"}},
            {"server": {"type": "stream_end", "turn_id": "new-turn",
                        "full_response": "answer to the new question"}},
        ])
        self.assertIn("answer to the new question", pane_texts(record["pane"]))


if __name__ == "__main__":
    unittest.main()
