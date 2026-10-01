# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A console frame that could not be processed must be reported as a fault.

`ConsoleShell._connect_and_read` wraps its whole read loop in one handler:

    except Exception:
        logger.exception("Message processing error")
    finally:
        self._add_message("system", "Disconnected.", "")

So ANY exception raised while processing a server frame — a frame shaped
differently from what a formatter walks, a render error, a type the formatters
do not expect — ends the read loop and reaches the person as the single word
"Disconnected.".  The traceback goes to `logger.exception` under
`logging.basicConfig(level=logging.WARNING)`, whose stderr output is covered by
the full-screen prompt_toolkit application, so it is not read either.

Two properties are pinned here, both consequences of the same handler:

* A fault is NAMED.  The console appended no line saying anything went wrong,
  so a fault is indistinguishable from an ordinary end of session and the
  person has no error text anywhere to read.
* The surfaces do not CONTRADICT each other.  The read loop stops, but nothing
  closes the socket and nothing clears the client's connected flag, so the HUD
  keeps rendering "connected" above a chat pane that says "Disconnected.".

Two control cases sit beside them, and they must PASS: they prove the stub
client really drives the production path (a well-formed frame renders its
report) and that the malformed frame really does raise inside
`_process_server_message` (so the silence above is the handler, not a stub that
delivered nothing).

Hermetic: no socket, no daemon, no auth token, no model server, no screen.
"""

from __future__ import annotations

import asyncio
import io
import unittest
from contextlib import redirect_stderr
from typing import Any
from unittest import mock

# The frame the stub answers connect() with — the shape web_server sends first.
CONNECTED: dict[str, Any] = {
    "type": "connected",
    "client_id": "console-test",
    "system_status": {"uptime_seconds": 12},
    "governance": {"autonomy_tier_name": "PROPOSE"},
}

# A health_report whose "layers" is a string instead of the list of layer dicts
# _format_health_report walks. The formatter iterates it and calls .get() on a
# single character, which raises inside _process_server_message.
BAD_FRAME: dict[str, Any] = {"type": "health_report", "layers": "degraded"}

# The same message, well formed — the control.
GOOD_FRAME: dict[str, Any] = {
    "type": "health_report",
    "layers": [{"name": "Web Server",
                "checks": [{"name": "Connections", "status": "green",
                            "summary": "1 active"}]}],
}

# Words a line has to carry for a person to read it as a fault rather than as
# the end of a session. "Disconnected." carries none of them.
FAULT_WORDS = ("error", "failed", "could not", "cannot", "problem",
               "unreadable", "unexpected", "fault")


def _names_a_fault(content: str) -> bool:
    return any(w in content.lower() for w in FAULT_WORDS)


class _StubClient:
    """Stands in for ConsoleClient: yields frames from a list, opens nothing.

    `connected` stays True exactly as the real client's does while its own
    reader task is alive — nothing in the read loop's failure path clears it.
    """

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = list(frames)
        self.connected = True
        self.closed = False

    async def connect(self) -> dict[str, Any]:
        return CONNECTED

    async def messages(self):
        for frame in self._frames:
            yield frame

    async def close(self) -> None:
        self.closed = True
        self.connected = False


def _no_models():
    """No Qwen-family model on the machine, so the view opens with no seeded
    attribution line and every message in it was appended by the read loop."""
    from intergen.model_manager import ModelManager
    return mock.patch.object(ModelManager, "list_downloaded", return_value=[])


def _shell():
    from intergen.console import shell
    # prompt_toolkit prints "Input is not a terminal" to stderr when an
    # Application is built under pytest; captured so it cannot pollute the run.
    with _no_models(), redirect_stderr(io.StringIO()):
        return shell.ConsoleShell()


def _read(frames: list[dict[str, Any]]):
    """Drive the real _connect_and_read over a stub client.

    Returns the shell and the (role, content) pairs the person would read.
    """
    sh = _shell()
    sh._client = _StubClient(frames)
    with redirect_stderr(io.StringIO()):
        asyncio.run(sh._connect_and_read())
    view = [(m.get("role", ""), m.get("content", "")) for m in sh._messages]
    return sh, view


class TheReadLoopReportsAFaultItCouldNotProcess(unittest.TestCase):

    def test_a_frame_that_could_not_be_processed_is_reported_as_an_error(self):
        sh, view = _read([BAD_FRAME])
        faults = [c for _role, c in view if _names_a_fault(c)]
        self.assertTrue(
            faults,
            "a frame the console could not process ended the read loop and the "
            "person was told nothing about it; the whole view reads "
            f"{[c for _r, c in view]!r}",
        )

    def test_the_hud_does_not_still_say_connected_after_the_loop_stopped(self):
        sh, view = _read([BAD_FRAME])
        # Premise: the chat pane does claim the session ended.
        self.assertTrue(any("Disconnected" in c for _r, c in view),
                        f"the read loop appended no status line: {view!r}")
        hud = "".join(text for _style, text in sh._render_hud())
        self.assertNotIn(
            "connected", hud.replace("disconnected", ""),
            "the HUD still reads connected above a chat pane that says "
            f"Disconnected.; HUD is {hud!r} and the view is "
            f"{[c for _r, c in view]!r}",
        )


class TheHarnessReachesTheProductionPath(unittest.TestCase):
    """Controls. These pass at this snapshot; they are what makes the two
    cases above evidence rather than an artifact of a stub that delivered
    nothing."""

    def test_a_well_formed_frame_is_carried_all_the_way_to_the_view(self):
        sh, view = _read([GOOD_FRAME])
        rendered = [c for role, c in view if role == "assistant"]
        self.assertTrue(any("SYSTEM HEALTH" in c for c in rendered),
                        f"the stub never reached the formatter: {view!r}")

    def test_the_malformed_frame_really_does_raise_inside_the_processor(self):
        sh = _shell()
        sh._client = _StubClient([])
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(AttributeError):
                asyncio.run(sh._process_server_message(BAD_FRAME))


if __name__ == "__main__":
    unittest.main()
