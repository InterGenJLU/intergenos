# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The web page's liveness failsafe after a review card.

The page arms a failsafe while a turn is the server's to finish: if nothing
arrives in time it reports "InterGen stopped responding partway through that",
and closes the socket to force a reconnect. A review card pauses it (the wait is
the person's) and the card's gate_resolved re-armed it, for the server to
finish the turn.

Some cards are not part of a turn, and some turns end without a terminal
frame, so the re-armed failsafe outlived the work it was watching and forced a
reconnect on a page whose server had done everything asked of it:

  * a card a command raised (/file): the command's tool_executed line ends it;
  * a card a new conversation ended: the server cancels the turn the card
    belonged to, and session_created is the last word on it.

The page now re-arms only while a turn is in flight, and a new conversation
ends whatever was in flight, as a switch already did. The control holds the
other side: a turn that goes silent after its card is still caught.

The page is run whole under node (web_page_harness) with the post-acknowledge
failsafe shortened to 80 ms, and each case waits it out.
"""

from __future__ import annotations

import unittest

from intergen.tests.web_page_harness import (
    NEEDS_NODE, NODE, connected, run_page,
)

_FAILSAFE_MS = 80
_WAIT = {"wait": 300}
_STOPPED = "stopped responding"


def _card(call_id: str, tool: str = "manage_services") -> dict:
    return {"server": {
        "type": "gate_prompt", "turn_id": "t1", "tool_call_id": call_id,
        "tool_name": tool, "action": "{}",
        "card": {"what": "restart the bluetooth service",
                 "command": "systemctl restart bluetooth",
                 "classification": "This changes the system.",
                 "footer": "No password needed.",
                 "actions": ["approve", "deny"]}}}


def _resolved(call_id: str, decision: str, reason: str = "") -> dict:
    frame = {"type": "gate_resolved", "tool_call_id": call_id,
             "decision": decision}
    if reason:
        frame["reason"] = reason
    return {"server": frame}


_A_TURN_WITH_A_CARD = [
    {"type": "restart bluetooth"},
    {"server": {"type": "turn_ack", "turn_id": "t1"}},
    {"server": {"type": "stream_start", "turn_id": "t1"}},
    _card("m1"),
]


def _run(steps: list) -> dict:
    return run_page(connected() + steps,
                    constants={"POST_ACK_TIMEOUT_MS": _FAILSAFE_MS})


def _forced_reconnects(record: dict) -> int:
    stopped = [b for b in record["banners"] if _STOPPED in b["text"]]
    return max(len(record["closes"]), len(stopped))


@unittest.skipUnless(NODE, NEEDS_NODE)
class ACardThatEndsTheWorkDoesNotForceAReconnect(unittest.TestCase):

    def test_a_card_a_command_raised(self) -> None:
        for decision, success in (("allow", True), ("deny", False)):
            with self.subTest(decision=decision):
                record = _run([
                    {"type": "/file /etc/hostname"},
                    {"server": {"type": "tool_ack", "tool_name": "read_file",
                                "tool_call_id": "f1"}},
                    _card("f1", "read_file"),
                    {"click_gate": decision},
                    _resolved("f1", decision),
                    {"server": {"type": "tool_executed",
                                "tool_name": "read_file", "success": success,
                                "summary": "done"}},
                    _WAIT,
                ])
                self.assertEqual(
                    _forced_reconnects(record), 0,
                    "the page reported that InterGen stopped responding and "
                    "closed its socket after a /file card whose command had "
                    f"finished: closes {record['closes']}, banners "
                    f"{record['banners']}")

    def test_a_card_a_new_conversation_ended(self) -> None:
        record = _run(_A_TURN_WITH_A_CARD + [
            {"type": "/new"},
            _resolved("m1", "deny",
                      "the conversation this card belonged to was ended"),
            {"server": {"type": "session_created", "client_id": "page-case",
                        "session_id": "session_new"}},
            {"server": {"type": "session_list", "sessions": [],
                        "current_session_id": "session_new"}},
            _WAIT,
        ])
        self.assertEqual(
            _forced_reconnects(record), 0,
            "the page forced a reconnect after the new conversation ended the "
            f"card's turn: closes {record['closes']}, banners "
            f"{record['banners']}")

    def test_a_card_a_switch_ended(self) -> None:
        """Control: a switch already ends what was in flight."""
        record = _run(_A_TURN_WITH_A_CARD + [
            _resolved("m1", "deny",
                      "the conversation this card belonged to was left"),
            {"server": {"type": "session_switched", "session_id": "other",
                        "message_count": 0, "messages": []}},
            _WAIT,
        ])
        self.assertEqual(_forced_reconnects(record), 0)

    def test_a_turn_that_goes_silent_after_its_card_is_still_caught(
            self) -> None:
        """Control: the failsafe still watches a turn after its card."""
        record = _run(_A_TURN_WITH_A_CARD + [
            {"click_gate": "allow"},
            _resolved("m1", "allow"),
            _WAIT,
        ])
        self.assertEqual(len(record["closes"]), 1,
                         "a turn that went silent after its card was not "
                         "caught by the failsafe")
        self.assertTrue(any(_STOPPED in b["text"] for b in record["banners"]))


if __name__ == "__main__":
    unittest.main()
