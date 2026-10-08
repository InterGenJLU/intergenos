# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Pasted text reaches the conversation, and the page says so only then.

The page's /paste command and its paste button open a box, and "Load into
Buffer" printed "Buffer loaded (N chars…)" and lit the 📎 buffer indicator —
and sent nothing. The text never reached the server, so the model never saw
it, while the page said it was loaded.

Pasted text now goes where a chosen file goes: the server's /file content
path (web_server.py ``_handle_file_command``), which puts it in the
conversation and answers ``file_loaded``. The page says it is loaded, and lights
the indicator, on that answer and not before; with no connection it says so
and claims nothing. The indicator goes out when the server empties the
conversation (``buffer_cleared``) or ends it (``session_created``).

The cases run the shipped app.js whole under node (web_page_harness.js).
"""

from __future__ import annotations

import shutil
import unittest

from intergen.tests.web_page_harness import (
    NEEDS_NODE, connected, pane_texts, run_page,
)

PASTED = "pasted words for the model"

# index.html starts the indicator out; the stand-in document has no markup.
_AS_THE_MARKUP_STARTS = {"classes": {"#buffer-indicator": "hidden"}}


def _paste(open_with: dict) -> list:
    """Open the paste box (by the button or by /paste), fill it, load it."""
    return [open_with, {"wait": 5},
            {"set": {"#modal-textarea": PASTED}},
            {"click": "#modal-paste-btn"}, {"wait": 5}]


def _loaded_frames(record: dict) -> list:
    return [f for f in record["sent"]
            if f.get("type") == "slash_command" and f.get("command") == "/file"]


def _claims(pane: list) -> list:
    return [t for t in pane_texts(pane) if "loaded" in t.lower()]


def _indicator(record: dict, at: int) -> str:
    return record["inspected"][at]["classes"]


@unittest.skipUnless(shutil.which("node"), NEEDS_NODE)
class APasteReachesTheServer(unittest.TestCase):

    def _check_a_paste(self, open_with: dict) -> None:
        record = run_page([_AS_THE_MARKUP_STARTS] + connected() + _paste(open_with) + [
            {"mark": "loaded, before the server answers"},
            {"inspect": "#buffer-indicator"},
            {"server": {"type": "file_loaded", "filename": "pasted text",
                        "chars": len(PASTED)}},
            {"mark": "after file_loaded"},
            {"inspect": "#buffer-indicator"},
        ])
        sent = _loaded_frames(record)
        self.assertEqual(
            len(sent), 1,
            "the pasted text was not sent: the page claimed it was loaded "
            f"while the model never saw it. Sent: {record['sent']!r}")
        self.assertEqual(sent[0].get("content"), PASTED)
        before, after = record["marks"]
        self.assertEqual(
            _claims(before["pane"]), [],
            "the page said the text was loaded before the server did: "
            f"{pane_texts(before['pane'])!r}")
        self.assertIn("hidden", _indicator(record, 0),
                      "the buffer indicator lit before the server answered")
        self.assertEqual(len(_claims(after["pane"])), 1,
                         f"no loaded line on file_loaded: "
                         f"{pane_texts(after['pane'])!r}")
        self.assertNotIn("hidden", _indicator(record, 1),
                         "the buffer indicator stayed out after file_loaded")

    def test_the_paste_button(self):
        self._check_a_paste({"click": "#btn-paste"})

    def test_the_paste_command(self):
        self._check_a_paste({"type": "/paste"})

    def test_a_paste_with_no_connection_says_so_and_claims_nothing(self):
        record = run_page([_AS_THE_MARKUP_STARTS] + _paste({"click": "#btn-paste"}) + [
            {"inspect": "#buffer-indicator"}])
        self.assertEqual(_loaded_frames(record), [])
        self.assertEqual(_claims(record["pane"]), [],
                         f"claimed loaded with no connection: "
                         f"{pane_texts(record['pane'])!r}")
        self.assertIn("hidden", _indicator(record, 0))
        self.assertTrue(
            any("Not connected" in b["text"] for b in record["banners"]),
            f"no word that nothing was sent: {record['banners']!r}")

    def test_the_indicator_goes_out_with_the_conversation(self):
        loaded = {"server": {"type": "file_loaded", "filename": "pasted text",
                             "chars": len(PASTED)}}
        for ending in ({"type": "buffer_cleared"},
                       {"type": "session_created", "client_id": "page-case"}):
            with self.subTest(ending=ending["type"]):
                record = run_page([_AS_THE_MARKUP_STARTS] + connected()
                                  + _paste({"click": "#btn-paste"})
                                  + [loaded, {"inspect": "#buffer-indicator"},
                                     {"server": ending},
                                     {"inspect": "#buffer-indicator"}])
                self.assertNotIn("hidden", _indicator(record, 0))
                self.assertIn(
                    "hidden", _indicator(record, 1),
                    f"the indicator still claims loaded text after "
                    f"{ending['type']}")


if __name__ == "__main__":
    unittest.main()
