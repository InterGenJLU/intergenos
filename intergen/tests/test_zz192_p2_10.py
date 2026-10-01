# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`intergen status` must say when the model servers were stopped ON PURPOSE.

The daemon already treats this as a correctness property. It carries the
game-launch pause as two first-class fields, and says why in its own words at
intergen/dbus_daemon.py:819-822:

    # Game-launch pause: True while the model servers are deliberately
    # stopped so a running game has the machine's memory. Reported as a
    # first-class field because "no model loaded" and "model paused on
    # purpose" must never look the same to anything reading status.
    "paused": self._paused,
    "paused_for": self._held_game_names(),

`intergen status` is the surface a person actually reads, so it is one of the
things reading status. It does not read either field: `print_status` never
mentions them (the whole of intergen/cli.py names the pause once, in an
unrelated docstring at line 364), and `cmd_status` hands the payload straight
to it without rendering anything of its own.

Nor does the degraded path cover the gap. `_model_server_down_now` returns None
while paused — deliberately, by its own docstring, because "the server is down
on purpose there and calling that a failure would be a lie in the other
direction" — so `model_server_down` is None, the DEGRADED branch does not fire,
and the render takes the plain `Running: {running}` path. The pause suppresses
the one signal the renderer does read, and the two fields that were supposed to
carry the truth instead are read by nobody.

What a person sees while a game holds the pause is therefore a machine that
says it is running, reports no error at all, and shows `[-] llama_server` two
lines below — byte for byte the report a machine gives when a model failed to
load. That is the collapse the daemon added the fields to prevent, at the
surface where it matters.

The daemon side is already pinned, in intergen/tests/test_game_pause.py
(`test_status_reports_the_pause_and_who_holds_it`): the payload carries the
fields. This file pins the other half — that the renderer says so — and it is
written the way its neighbour
intergen/tests/test_status_reports_the_model_server.py is written: a renderer
contract tested against payloads, because `print_status` is pure over its dict
by design and a renderer reachable only through a live bus could not be tested
for what it says. No bus, no daemon, no game, no model server.
"""
from __future__ import annotations

import contextlib
import io
import unittest

from intergen import cli


def _render(status: dict) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.print_status(status)
    return buf.getvalue()


def _line(text: str, label: str) -> str:
    for ln in text.splitlines():
        if ln.strip().startswith(label):
            return ln.strip()
    raise AssertionError(f"no {label!r} line in:\n{text}")


# The payload a daemon builds while a game holds the pause, field for field as
# dbus_daemon.status() assembles it: the daemon is up and _running is untouched
# by pause_for_game, both servers have been stopped by _stop_servers_for_pause,
# and model_server_down is None because _model_server_down_now falls silent
# while _paused.
PAUSED_FOR_A_GAME = {
    "running": True,
    "version": "0.1.0",
    "last_error": None,
    "model_server_down": None,
    "paused": True,
    "paused_for": ["steam_app_620"],
    "requests_handled": 3,
    "components": {
        "hardware_detector": True,
        "model_manager": True,
        "llama_server": False,
        "router": True,
    },
}

# The control: the same machine with nothing holding the pause. Whatever the
# paused report gains must not appear here, or the line stops meaning anything.
NOT_PAUSED = {
    "running": True,
    "version": "0.1.0",
    "last_error": None,
    "model_server_down": None,
    "paused": False,
    "paused_for": [],
    "requests_handled": 3,
    "components": {"llama_server": True},
}


class TheStatusReportSaysTheAssistantIsPausedTest(unittest.TestCase):

    def test_status_says_the_assistant_is_paused_for_a_game(self):
        """The defect verbatim: nothing in the whole report names the pause."""
        out = _render(PAUSED_FOR_A_GAME)
        self.assertIn("paus", out.lower(),
                      "the report never says the assistant is paused:\n" + out)

    def test_status_names_the_game_holding_the_pause(self):
        """`paused_for` is the daemon's answer to "why", and it is carried so a
        reader can be told which window to close to get the assistant back."""
        out = _render(PAUSED_FOR_A_GAME)
        self.assertIn("steam_app_620", out,
                      "the report never names the game holding the pause:\n"
                      + out)

    def test_a_paused_machine_does_not_report_a_bare_running_true(self):
        """"Running: True" beside a stopped chat server is the exact sentence
        the model-server work already removed for the failure case. A pause is
        not a failure, but it is not health either, and the two must not print
        the same line."""
        line = _line(_render(PAUSED_FOR_A_GAME), "Running:")
        self.assertNotEqual("Running:    True", line)

    def test_the_stopped_component_is_explained_rather_than_left_bare(self):
        """`[-] llama_server` with no sentence anywhere saying the stop was
        deliberate reads as a model that failed to load — which is precisely
        the collapse the daemon's two fields exist to prevent."""
        out = _render(PAUSED_FOR_A_GAME)
        self.assertIn("[-] llama_server", out)
        self.assertTrue(
            any("paus" in ln.lower() for ln in out.splitlines()
                if not ln.strip().startswith("[")),
            "the only trace of the stopped server is the component marker; "
            "nothing says it was stopped on purpose:\n" + out)


class ThePauseReportIsNotPrintedOnAnUnpausedMachineTest(unittest.TestCase):
    """The control pair: a gate that fires on every machine proves nothing."""

    def test_an_unpaused_machine_does_not_mention_a_pause(self):
        out = _render(NOT_PAUSED)
        self.assertNotIn("paus", out.lower())

    def test_an_unpaused_machine_keeps_its_running_true(self):
        self.assertIn("True", _line(_render(NOT_PAUSED), "Running:"))


if __name__ == "__main__":  # pragma: no cover - direct invocation convenience
    unittest.main()
