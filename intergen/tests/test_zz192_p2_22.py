# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A terminal-console turn that delivered no text is never shown as the answer.

The console's stream terminal reads the kept answer off the frame with a
default::

    full = msg.get("full_response", self._streaming_buffer)

``dict.get`` substitutes the default ONLY when the key is absent. A frame that
carries ``full_response`` as an explicit empty string — the shape of a turn
that produced no text — therefore yields an empty ``full``, which the next
statement appends as the assistant's answer and counts in the session rollup.
The streamed tokens the person already watched arrive are then dropped on the
line after.

The same terminal on the other two surfaces refuses that frame:

* the web conversation view falls back on falsiness, not on absence
  (``const full = msg.full_response || state.streamingContent;`` —
  intergen/web/app.js), so an empty string there keeps the streamed text;
* the command line treats an empty reply as a turn that answered nothing
  (``answered = bool(response.strip())`` — intergen/cli.py) and exits
  non-zero rather than printing a blank answer.

And the server's own streamed path says the rule plainly where it guards the
toolless case: "never leave the user staring at a blank reply". That guard is
conditioned on ``not tool_results``, so a turn whose tool call failed or was
blocked while the model emitted no text still carries ``full_response: ""`` to
the client, and the console is the one surface that renders it.

These cases pin the two properties the quoted line itself promises:

* an assistant panel is never delivered with no text in it, and
* what the person watched arrive is not discarded in favour of nothing.

Two controls run beside them so the instrument is shown to read a true
positive: a frame carrying text is delivered, and a frame with NO
``full_response`` key falls back to the buffer exactly as the default intends.
That isolates the defect to the explicit empty string rather than to the
fallback as a whole.
"""

from __future__ import annotations

import asyncio
import io
import re
import unittest
from contextlib import redirect_stderr
from typing import Any
from unittest import mock

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# What the person watched arrive, token by token, before the terminal frame.
STREAMED = "partial text"


def _no_models():
    """Keep the seeded view empty and the case hermetic.

    ``ConsoleShell.__init__`` seeds its message list from
    ``startup_messages()``, which asks the machine which models it holds. The
    neighbouring console case patches the same seam for the same reason: what
    is on this box must not decide what this file asserts.
    """
    from intergen.model_manager import ModelManager
    return mock.patch.object(ModelManager, "list_downloaded", return_value=[])


def _shell():
    """A console shell with no daemon, no socket and no terminal.

    prompt_toolkit prints "Input is not a terminal" to stderr when built under
    pytest. It is captured here so it cannot pollute a suite capture, and it
    says nothing about this behaviour either way.
    """
    from intergen.console import shell
    with _no_models(), redirect_stderr(io.StringIO()):
        return shell.ConsoleShell()


def _drive(sh: Any, frames: list[dict]) -> None:
    """Feed the frames to the shell in wire order, as the client's reader does."""
    async def _run() -> None:
        for frame in frames:
            await sh._process_server_message(frame)
    asyncio.run(_run())


def _assistant(sh: Any) -> list[dict]:
    return [m for m in sh._messages if m.get("role") == "assistant"]


def _system(sh: Any) -> list[dict]:
    return [m for m in sh._messages if m.get("role") == "system"]


def _rendered(sh: Any) -> str:
    """The chat as the person would see it: the ANSI bridge unwrapped and the
    colour codes stripped, so a failure prints the panel a person would read."""
    view = sh._render_chat()
    raw = getattr(view, "value", None) or str(view)
    return _ANSI_RE.sub("", raw)


# The real wire order for a turn that streamed a partial answer and then
# delivered nothing: stream_start, one token, then the terminal frame carrying
# full_response as an explicit empty string.
EMPTY_TURN = [
    {"type": "stream_start"},
    {"type": "stream_token", "token": STREAMED},
    {"type": "stream_end", "full_response": "", "source": "llm_freeform"},
]


class AnEmptyStreamTerminalTests(unittest.TestCase):
    """`intergen console` — its own renderer, never the web view."""

    def test_an_empty_stream_end_is_not_delivered_as_an_answer(self):
        sh = _shell()
        _drive(sh, EMPTY_TURN)

        blank = [m for m in _assistant(sh) if not m.get("content", "").strip()]
        self.assertEqual(
            blank, [],
            "a stream_end carrying full_response=\"\" was delivered as the "
            "assistant's answer: an assistant message with no text was "
            f"appended, which renders as an empty InterGen panel — {blank!r}")

        # Nothing about the turn may be silent. Either the text the person
        # watched arrive is the answer (what the quoted default promises), or
        # the console says the turn produced no answer. Wording is not pinned
        # here — only that one of the two happened.
        delivered = "\n".join(m.get("content", "") for m in _assistant(sh))
        kept = STREAMED in delivered
        explained = bool(_system(sh))
        self.assertTrue(
            kept or explained,
            "the turn delivered nothing and said nothing: the streamed text "
            f"{STREAMED!r} was discarded and no line explains the empty turn "
            f"(messages: {sh._messages!r})")

    def test_an_empty_stream_end_does_not_count_a_turn(self):
        sh = _shell()
        _drive(sh, EMPTY_TURN)
        self.assertEqual(
            sh._session["turns"], 0,
            "a turn that delivered no text was counted in the session rollup, "
            "so the console reports having answered a question it did not "
            f"answer (session: {sh._session!r})")

    def test_the_view_is_not_left_blank_for_a_turn_that_delivered_nothing(self):
        """The rendered counterpart of the first case, and the outcome a person
        actually meets. Fix-agnostic: either acceptable repair puts something on
        the screen — the streamed text, or a line saying the turn produced no
        answer. Today neither is there, and the view holds an InterGen panel
        with nothing between its borders."""
        sh = _shell()
        _drive(sh, EMPTY_TURN)
        view = _rendered(sh)
        shown = STREAMED in view or any(
            m.get("content", "").strip() in view and m.get("content", "").strip()
            for m in _system(sh))
        self.assertTrue(
            shown,
            "the view says nothing about the turn: the text the person watched "
            "arrive was discarded and no line replaced it, so the answer is an "
            f"empty panel —\n{view}")

    # -- controls: the instrument reads a true positive ---------------------

    def test_a_stream_end_that_carries_text_is_delivered(self):
        sh = _shell()
        _drive(sh, [
            {"type": "stream_start"},
            {"type": "stream_token", "token": STREAMED},
            {"type": "stream_end", "full_response": "the kept answer",
             "source": "llm_freeform"},
        ])
        self.assertEqual([m.get("content") for m in _assistant(sh)],
                         ["the kept answer"])
        self.assertEqual(sh._session["turns"], 1)

    def test_a_stream_end_with_no_full_response_key_falls_back_to_the_buffer(self):
        """The default fires on an ABSENT key — which is the whole of what
        ``dict.get`` gives, and the reason the empty string above is a defect
        rather than a missing feature."""
        sh = _shell()
        _drive(sh, [
            {"type": "stream_start"},
            {"type": "stream_token", "token": STREAMED},
            {"type": "stream_end", "source": "llm_freeform"},
        ])
        self.assertEqual([m.get("content") for m in _assistant(sh)], [STREAMED])


if __name__ == "__main__":
    unittest.main()
