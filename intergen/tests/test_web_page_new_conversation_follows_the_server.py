# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A new conversation on the web page is shown when the server has made it.

/new and the New-session button emptied the pane and printed "New
conversation started." the moment they were used, before the server had ended
anything, and send() drops a frame without a word when the socket is not
open: with the connection down, the page announced a new conversation that the
server never made. A session switch is already painted from the server's
session_switched for the same reason.

The page now asks the server and paints the new conversation from its
session_created frame; with no open connection it says so and changes nothing.
The page is run whole under node (web_page_harness).
"""

from __future__ import annotations

import unittest

from intergen.tests.web_page_harness import (
    NEEDS_NODE, NODE, connected, pane_texts, run_page, sent_types,
)

_A_TURN = [
    {"type": "what time is it in Tokyo"},
    {"server": {"type": "turn_ack", "turn_id": "t1"}},
    {"server": {"type": "stream_start", "turn_id": "t1"}},
    {"server": {"type": "stream_end", "turn_id": "t1",
                "full_response": "It is evening there."}},
]
_THE_TURN = ["what time is it in Tokyo", "It is evening there."]
_CREATED = {"server": {"type": "session_created", "client_id": "page-case",
                       "session_id": "session_new"}}


@unittest.skipUnless(NODE, NEEDS_NODE)
class ANewConversationFollowsTheServer(unittest.TestCase):

    def _check(self, start: dict) -> None:
        record = run_page(connected() + _A_TURN + [
            start, {"mark": "before the server answered"}, _CREATED])
        self.assertEqual(sent_types(record)[-1], "new_session")
        self.assertEqual(
            pane_texts(record["marks"][0]["pane"]), _THE_TURN,
            "the pane announced a new conversation before the server had "
            "made one")
        self.assertEqual(pane_texts(record["pane"]),
                         ["New conversation started."])

    def test_new_is_painted_from_session_created(self) -> None:
        self._check({"type": "/new"})

    def test_the_new_session_button_is_painted_from_session_created(
            self) -> None:
        self._check({"click": "#btn-new-session"})

    def test_new_without_a_connection_says_so_and_changes_nothing(
            self) -> None:
        for start in ({"type": "/new"}, {"click": "#btn-new-session"}):
            with self.subTest(start=start):
                record = run_page(connected() + _A_TURN + [
                    {"close_from_server": True}, start])
                self.assertEqual(sent_types(record), ["message"])
                self.assertEqual(
                    pane_texts(record["pane"]), _THE_TURN,
                    "the pane announced a new conversation although nothing "
                    "reached the server")
                self.assertTrue(
                    any("Not connected" in b["text"]
                        for b in record["banners"]),
                    f"the person was not told: {record['banners']}")


if __name__ == "__main__":
    unittest.main()
