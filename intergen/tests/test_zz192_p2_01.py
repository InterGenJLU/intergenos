# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The terminal console must not present an unanswered turn as an answer.

THE RULE. ``_deliver_answer`` states the rule the command line keeps
(intergen/cli.py:349-377): a reply the assistant itself declares it did not
handle is not an answer — "Returns True when the turn delivered an answer and
False when it did not: an empty reply, or a turn the reply itself declares
produced nothing. The caller exits non-zero on False." The declaration it reads
is the ``handled`` field the reply carries.

THE SAME DECLARATION RIDES THE CONSOLE'S WIRE. The console is its own renderer
over its own WebSocket, and the web delivery stamps ``"handled":
result.handled`` on every non-streamed ``response`` frame it sends — the fast
path (intergen/web_server.py:1299-1308) and the fallback that carries every
other non-streamed source (intergen/web_server.py:1365-1372).

WHAT THE CONSOLE DOES WITH IT. ``ConsoleShell._process_server_message``'s
``response`` branch (intergen/console/shell.py:656-665) renders every frame
through ``_add_message("assistant", …)`` and rolls it into the session totals
through ``_build_telemetry``. Neither the five keys ``_add_message`` writes nor
the six ``_build_telemetry`` returns has a place for the declaration, so the
field is dropped at the boundary: nothing the console stores, and nothing it
can render from what it stores, says the turn went unanswered.

THE INPUT USED HERE IS THE TREE'S OWN, ON THE CONSOLE'S OWN PATH. The router
returns ``RouteResult(source="conversation_unbound", handled=False)`` with the
"so I have not answered it" sentence when a turn does not name its conversation
(intergen/router.py:2433-2441). That source is outside both delivery
allowlists, so it leaves by the fallback above, as a ``response`` frame
carrying ``handled: False``. The project's own battery already classes that
source and that sentence as a turn that was NOT answered
(intergen/tests/web_battery.py:206-219) — by matching text and route name,
because nothing on any surface reads the field the daemon actually sets.

Hermetic: no service, no socket, no model server, no network. The shell is
built the way the console attribution case builds it, with the model manifest
lookup stubbed, and the frame is fed straight into the message handler.
"""

from __future__ import annotations

import asyncio
import io
import unittest
from contextlib import redirect_stderr
from unittest import mock


def _new_shell():
    """A ConsoleShell with an empty view and a zeroed session rollup.

    ``ConsoleShell()`` seeds its view from ``startup_messages()``, which reads
    the machine's model manifest for the payload attribution. That is stubbed
    empty so this case says nothing about which models are on the box, and
    prompt_toolkit's "Input is not a terminal" notice is captured for the same
    reason the attribution case captures it.
    """
    from intergen.console import shell
    from intergen.model_manager import ModelManager

    with mock.patch.object(ModelManager, "list_downloaded", return_value=[]), \
            redirect_stderr(io.StringIO()):
        sh = shell.ConsoleShell()
    sh._messages.clear()
    sh._session = {"turns": 0, "llm_hits": 0, "tool_hits": 0}
    return sh


def _unhandled_response_frame() -> dict:
    """The frame the web delivery builds for the unbound-conversation refusal.

    Built from the router's OWN result object so the text, the route name and
    the declaration are the shipped ones, then shaped with the exact keys the
    fallback delivery sends (intergen/web_server.py:1365-1372).
    """
    from intergen.interfaces.types import RouteResult

    result = RouteResult(
        text=("Something went wrong on my side: I could not tell which "
              "conversation this message belongs to, so I have not "
              "answered it. Please try again."),
        source="conversation_unbound",
        handled=False,
    )
    return {
        "type": "response",
        "turn_id": "turn-1",
        "content": result.text,
        "source": result.source,
        "handled": result.handled,
        "confidence": None,
    }


class TheConsoleReadsTheTurnsOwnDeclaration(unittest.TestCase):
    """intergen/console/shell.py — the surface a person works at in a terminal."""

    def test_the_delivery_puts_the_declaration_on_the_wire(self):
        """Control: the console is not missing the field because nobody sent
        it. The non-streamed dispositions stamp the reply's own declaration on
        the frame, so there is something on the console's wire to read."""
        import inspect
        from intergen import web_server

        src = inspect.getsource(web_server.WebServer._handle_client_message)
        self.assertIn(
            '"handled": result.handled', src,
            "the web delivery no longer carries the turn's own declaration; "
            "this case's premise has moved and it must be re-derived")

    def test_a_handled_answer_still_renders_as_an_answer(self):
        """Control: the instrument passes on a turn that WAS answered, so a
        red below is about the declaration and not about the harness."""
        sh = _new_shell()
        asyncio.run(sh._process_server_message({
            "type": "response",
            "turn_id": "turn-0",
            "content": "Linux was first released in 1991.",
            "source": "semantic",
            "handled": True,
        }))
        self.assertEqual(len(sh._messages), 1)
        self.assertEqual(sh._messages[-1].get("role"), "assistant")

    def test_the_console_does_not_render_an_unhandled_turn_as_an_answer(self):
        """A turn the reply itself declares produced nothing is not an answer.

        The console may still show the text — the person should read it — but
        what it stores for the turn has to keep the declaration, so the view
        can say the turn went unanswered. Either the entry carries the
        declaration (on itself or on its telemetry), or it is not filed as an
        InterGen answer.
        """
        sh = _new_shell()
        asyncio.run(sh._process_server_message(_unhandled_response_frame()))

        self.assertEqual(len(sh._messages), 1,
                         "the turn produced no view entry at all")
        entry = sh._messages[-1]
        telemetry = entry.get("telemetry") or {}
        declared = entry.get("handled", telemetry.get("handled"))

        self.assertTrue(
            declared is False or entry.get("role") != "assistant",
            "the console filed a turn the server declared unhandled "
            "(handled=False) as an InterGen answer and kept no record of the "
            "declaration: role="
            f"{entry.get('role')!r}, source={entry.get('source')!r}, "
            f"entry keys={sorted(entry)}, "
            f"telemetry keys={sorted(telemetry)}, "
            f"session rollup={sh._session!r}")


if __name__ == "__main__":
    unittest.main()
