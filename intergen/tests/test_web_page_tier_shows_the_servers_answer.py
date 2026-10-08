# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""/tier on the page shows the server's answer.

The page sends /tier to the server, and the server answers with a
``tier_status`` frame (web_server.py ``_handle_slash_command``): the
governance engine's snapshot, or "unavailable" when no engine is running. The
page had no handler for that frame, so /tier showed nothing at all — a command
the help lists ("/tier  Show current autonomy tier") that answered with
silence.

The page now renders the frame: the tier by name and number, and, when the
server has no governance engine, that the tier is not available. The HUD's
tier follows the same snapshot.

The cases run the shipped app.js whole under node (web_page_harness.js).
"""

from __future__ import annotations

import shutil
import unittest

from intergen.tests.web_page_harness import (
    NEEDS_NODE, connected, pane_texts, run_page,
)

SNAPSHOT = {"autonomy_tier": 1, "autonomy_tier_name": "PROPOSE",
            "healthy": True, "hash_verified": True, "active_cooldowns": 0}


def _tier_lines(record: dict) -> list:
    return [t for t in pane_texts(record["pane"]) if "tier" in t.lower()]


@unittest.skipUnless(shutil.which("node"), NEEDS_NODE)
class TierShowsTheServersAnswer(unittest.TestCase):

    def test_the_tier_the_server_reports_is_shown(self):
        record = run_page(connected() + [
            {"type": "/tier"},
            {"server": {"type": "tier_status", "tier": SNAPSHOT}},
            {"inspect": "#hud-tier .hud-value"},
        ])
        lines = _tier_lines(record)
        self.assertTrue(
            any("PROPOSE" in t for t in lines),
            "the server answered /tier and the page showed nothing: "
            f"{pane_texts(record['pane'])!r}")
        self.assertEqual(record["inspected"][0]["text"], "PROPOSE",
                         "the HUD's tier did not follow the server's answer")

    def test_no_governance_engine_is_said(self):
        record = run_page(connected() + [
            {"type": "/tier"},
            {"server": {"type": "tier_status", "tier": "unavailable"}},
        ])
        lines = _tier_lines(record)
        self.assertTrue(
            any("not available" in t for t in lines),
            "the server said the tier is unavailable and the page showed "
            f"nothing: {pane_texts(record['pane'])!r}")

    def test_control_tier_is_sent_to_the_server(self):
        record = run_page(connected() + [{"type": "/tier"}])
        self.assertIn({"type": "slash_command", "command": "/tier"},
                      record["sent"])


if __name__ == "__main__":
    unittest.main()
