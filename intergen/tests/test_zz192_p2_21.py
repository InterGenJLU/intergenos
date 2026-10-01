# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A failed turn must end the console's stream state (review item P2-21).

THE FRAME SEQUENCE THIS PINS. The daemon opens a streamed turn with
`stream_start`, sends `stream_token` frames, and normally closes it with
`stream_end`. When the model worker raises mid-stream the exception leaves
`_process_llm_stream`, leaves `_stream_llm_response`, and is caught by the
no-wedge backstop in `_run_turn`, which sends an `error` frame instead. No
`stream_end` is ever sent for that turn. So the wire really can carry
`stream_start` → `stream_token`* → `error`, and the console has to treat that
`error` as the end of the turn.

WHAT THE TERMINAL CONSOLE DOES TODAY. `ConsoleShell._process_server_message`'s
`error` branch appends one system line and touches nothing else: neither
`_is_streaming` nor `_streaming_buffer` is cleared. `_render_hud` keeps
emitting the `class:stream-cursor` thinking indicator while `_is_streaming` is
true, and `_render_chat` keeps drawing `_streaming_buffer` inside an
"InterGen" answer panel with a cursor block appended, for the rest of the
session or until the next `stream_start`. A turn that failed therefore reads
as an answer still arriving, with the error line scrolled above it as one more
dim system message.

WHY THIS IS THE BEHAVIOUR THE PROJECT ALREADY CHOSE. The browser surface
handles the same frame in `handleError` (intergen/web/app.js) and does exactly
this teardown — `stopThinking()` plus hiding the streaming container — under
the comment "Clear the thinking state on any error so the mark doesn't glow
forever." The two surfaces are supposed to agree about what an error frame
means; the console is the one that drifted.

WHAT THE CASES ASSERT. Only that the stream is over: `_is_streaming` false,
`_streaming_buffer` empty, no thinking indicator in the HUD, and no live
cursor drawn after the partial text. That is deliberately agnostic about the
remedy — a fix that discards the partial text (the browser's choice) and a fix
that promotes it into a finished, cursor-less answer panel both satisfy every
assertion here. The controls beside them drive `stream_end` and a bare
`error` with no stream open, so the same assertions are shown passing on the
paths that already behave.

Hermetic: no daemon, no socket, no model server, no network, no screen. The
shell is constructed directly and fed dict frames, exactly as the wire would
deliver them.
"""

from __future__ import annotations

import asyncio
import io
import unittest
from contextlib import redirect_stderr
from unittest import mock

from prompt_toolkit.formatted_text import fragment_list_to_text, to_formatted_text

# The three frames of a turn that starts streaming and then fails.
STREAM_START = {"type": "stream_start", "turn_id": "t1",
                "source": "llm_freeform", "model_name": "local"}
STREAM_TOKEN = {"type": "stream_token", "turn_id": "t1", "token": "partial "}
ERROR = {"type": "error", "code": "llm", "message": "engine unreachable"}
# The frame the same turn sends when it succeeds — the control.
STREAM_END = {"type": "stream_end", "turn_id": "t1",
              "full_response": "partial answer", "source": "llm_freeform"}


def _shell():
    """Build a ConsoleShell with nothing behind it.

    prompt_toolkit writes "Input is not a terminal" to stderr when an
    Application is built under pytest; it is captured so it cannot pollute a
    suite capture, and it says nothing about this behaviour either way.
    list_downloaded is stubbed so the seeded attribution line never depends on
    which models happen to be on the machine running the case.
    """
    from intergen.console import shell
    from intergen.model_manager import ModelManager
    with mock.patch.object(ModelManager, "list_downloaded", return_value=[]), \
            redirect_stderr(io.StringIO()):
        return shell.ConsoleShell()


def _feed(sh, frames):
    """Deliver frames to the shell in order, as the reader loop would."""
    async def _drive() -> None:
        for frame in frames:
            await sh._process_server_message(frame)
    asyncio.run(_drive())


def _hud_text(sh) -> str:
    return "".join(text for _style, text in sh._render_hud())


def _hud_has_stream_cursor(sh) -> bool:
    return any(style == "class:stream-cursor" for style, _text in sh._render_hud())


def _chat_text(sh) -> str:
    return fragment_list_to_text(to_formatted_text(sh._render_chat()))


class AnErrorFrameEndsTheStreamTests(unittest.TestCase):
    """stream_start → stream_token → error leaves no stream behind."""

    def test_an_error_frame_ends_the_stream_state(self):
        sh = _shell()
        _feed(sh, [STREAM_START, STREAM_TOKEN, ERROR])
        self.assertFalse(
            sh._is_streaming,
            "the error frame ended the turn but the console is still "
            "streaming, so the HUD keeps saying the assistant is thinking")
        self.assertEqual(
            sh._streaming_buffer, "",
            "the error frame ended the turn but the partial model text is "
            "still buffered, so the view keeps drawing it as a live answer")

    def test_the_hud_stops_saying_the_assistant_is_thinking(self):
        sh = _shell()
        _feed(sh, [STREAM_START, STREAM_TOKEN, ERROR])
        self.assertFalse(
            _hud_has_stream_cursor(sh),
            "the HUD still carries the thinking indicator after a failed "
            f"turn: {_hud_text(sh)!r}")

    def test_the_view_stops_drawing_a_live_cursor_after_the_partial_text(self):
        sh = _shell()
        _feed(sh, [STREAM_START, STREAM_TOKEN, ERROR])
        rendered = _chat_text(sh)
        self.assertIn("Error [llm]: engine unreachable", rendered,
                      "the error was never rendered at all, so this case is "
                      "not measuring what it claims to measure")
        self.assertNotIn(
            "partial █", rendered,
            "the failed turn is still drawn as an answer arriving — the "
            "partial text carries a live cursor block below the error "
            f"line:\n{rendered}")

    def test_the_error_is_not_swallowed_by_the_teardown(self):
        """The turn ends AND the person is told why. Clearing the stream may
        not cost the error line."""
        sh = _shell()
        _feed(sh, [STREAM_START, STREAM_TOKEN, ERROR])
        self.assertTrue(
            any("Error [llm]: engine unreachable" in m.get("content", "")
                for m in sh._messages),
            f"no message carried the error: {sh._messages!r}")


class TheControlsTests(unittest.TestCase):
    """The same assertions on the paths that already behave, so a red above
    is the console's behaviour and not a mis-wired harness."""

    def test_stream_end_ends_the_stream_state(self):
        sh = _shell()
        _feed(sh, [STREAM_START, STREAM_TOKEN, STREAM_END])
        self.assertFalse(sh._is_streaming)
        self.assertEqual(sh._streaming_buffer, "")
        self.assertFalse(_hud_has_stream_cursor(sh))

    def test_an_error_with_no_stream_open_stays_quiet(self):
        sh = _shell()
        _feed(sh, [ERROR])
        self.assertFalse(sh._is_streaming)
        self.assertEqual(sh._streaming_buffer, "")
        self.assertFalse(_hud_has_stream_cursor(sh))

    def test_the_frames_reached_the_handler(self):
        """Before the error: the stream really was open and buffering, so the
        cases above start from the state they describe."""
        sh = _shell()
        _feed(sh, [STREAM_START, STREAM_TOKEN])
        self.assertTrue(sh._is_streaming)
        self.assertEqual(sh._streaming_buffer, "partial ")


if __name__ == "__main__":
    unittest.main()
