# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""An unrecognised /model tier is refused, not silently coerced to medium.

The terminal console's slash handler resolves the tier of a `/model` command
with a single expression:

    tier = args if args in ("small", "medium", "large") else "medium"

so ANY argument outside the three — a typo (`/model larg`), a model name
(`/model 9b`), or no argument at all — is discarded and replaced with
"medium". The console then sends a switch_model frame for "medium" and tells
the person "Model tier requested: medium".

Two properties are pinned here, and neither is about typing:

* THE PERSON'S INPUT IS NAMED, NOT SWALLOWED. A command the console did not
  understand has to say so. Reporting "Model tier requested: medium" to
  someone who typed `larg` is a false statement about what they asked for,
  and it is indistinguishable, on the view, from a tier change they did
  intend — so the next thing they read about the served model is wrong and
  they have no way to tell.

* NO FRAME IS SENT FOR A REQUEST NOBODY MADE. The daemon's own handler
  (web_server._handle_switch_model) DOES validate the tier and answers an
  unknown one with an invalid_model_tier error. That guard can never fire for
  a typo, because the coercion happens on the client, one hop upstream: the
  daemon is handed a valid "medium" and has nothing to object to. A check
  placed where the bad value can no longer arrive is not a check, and the
  fix belongs at the console, which is the only place that still holds what
  the person actually typed.

The recognised-tier case is asserted alongside, as the instrument's true
positive: a case that could pass because `/model` stopped working altogether
would certify nothing.
"""

from __future__ import annotations

import asyncio
import io
import unittest
from contextlib import redirect_stderr
from unittest import mock


class _RecordingClient:
    """Stands in for ConsoleClient; records every frame the shell sends."""

    def __init__(self):
        self.sent: list[dict] = []
        self.connected = True

    async def send(self, payload):
        self.sent.append(payload)

    async def send_message(self, content):
        self.sent.append({"type": "message", "content": content})

    def of_type(self, type_name):
        return [m for m in self.sent if m.get("type") == type_name]


def _shell():
    """A ConsoleShell with a recording client and no real model on the box.

    ModelManager.list_downloaded is stubbed empty so the seeded startup
    messages stay out of the way and the view starts clean — this case is
    about the /model command, not about which models this machine holds.
    prompt_toolkit prints "Input is not a terminal" to stderr when built
    under pytest; it is captured so it cannot pollute a suite capture, and it
    says nothing about this behaviour either way.
    """
    from intergen.model_manager import ModelManager
    from intergen.console import shell as shell_mod

    with mock.patch.object(ModelManager, "list_downloaded", return_value=[]), \
            redirect_stderr(io.StringIO()):
        sh = shell_mod.ConsoleShell()
    client = _RecordingClient()
    sh._client = client
    return sh, client


def _system_text(sh) -> str:
    return "\n".join(m.get("content", "") for m in sh._messages
                     if m.get("role") == "system")


class AnUnrecognisedModelTierTests(unittest.TestCase):

    def test_an_unrecognised_model_tier_is_refused_not_coerced(self):
        sh, client = _shell()

        asyncio.run(sh._handle_slash_command("/model larg"))

        said = _system_text(sh)
        self.assertEqual(
            client.of_type("switch_model"), [],
            "the console sent a model switch nobody asked for "
            f"(frames={client.sent!r}, and it told the person {said!r})")

    def test_the_console_does_not_report_a_tier_the_person_never_typed(self):
        """The view half of the same defect, asserted on its own so it is not
        hidden behind the frame assertion above: whatever the console decides
        to do with `larg`, what it PRINTS may not be a tier the person did not
        type."""
        sh, _client = _shell()

        asyncio.run(sh._handle_slash_command("/model larg"))

        said = _system_text(sh)
        self.assertNotIn(
            "Model tier requested: medium", said,
            "the console reported a request the person never made; "
            f"it said: {said!r}")
        self.assertIn(
            "larg", said,
            "the console did not name the tier it failed to understand; "
            f"it said: {said!r}")

    def test_a_model_name_is_not_read_as_a_tier(self):
        """Same defect, an input a person would plausibly type: the argument
        names a model, not one of the three tiers."""
        sh, client = _shell()

        asyncio.run(sh._handle_slash_command("/model 9b"))

        said = _system_text(sh)
        self.assertEqual(
            client.of_type("switch_model"), [],
            f"a model name was accepted as a tier switch: {client.sent!r}")
        self.assertIn("9b", said,
                      f"the console did not name the argument: {said!r}")

    def test_a_bare_model_command_requests_no_switch(self):
        """`/model` with no argument is a question, not an instruction. It may
        report the tier or say how to set one; it may not pick a tier on the
        person's behalf and announce it as their request."""
        sh, client = _shell()

        asyncio.run(sh._handle_slash_command("/model"))

        self.assertEqual(
            client.of_type("switch_model"), [],
            "a bare /model switched the tier on its own: "
            f"{client.sent!r}")

    def test_a_recognised_tier_still_reaches_the_daemon(self):
        """The instrument's true positive. Without this, a console that had
        simply dropped /model would satisfy every case above."""
        sh, client = _shell()

        asyncio.run(sh._handle_slash_command("/model large"))

        frames = client.of_type("switch_model")
        self.assertEqual(len(frames), 1,
                         f"a valid tier did not reach the daemon: {client.sent!r}")
        self.assertEqual(frames[0].get("tier"), "large")
        self.assertIn("large", _system_text(sh))


if __name__ == "__main__":
    unittest.main()
