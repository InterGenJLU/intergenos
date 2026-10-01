# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The frontier method's fail-safe holds on a daemon whose start stopped early.

``InterGenDaemon.escalate`` documents its own contract: "no manager / no
provider / declined consent / error all return a clean JSON note; nothing is
sent without an explicit Send."  Three readings of that promise are taken here,
each against the daemon's own method and nothing else:

  1. A daemon whose ``start_service()`` returned at the single-instance guard
     never reached the step that builds the escalation manager, so the
     attribute the "no manager" branch reads was never written.  ``intergen
     ask-frontier --direct`` constructs exactly such a daemon on a box where
     sole ownership of the bus name cannot be VERIFIED (no gi/Gio, or the
     session bus unreachable): the guard is fail-closed and returns, the
     command then calls the frontier method on the daemon it just built.
  2. The same shape reached the other way — the partial construction several
     of this tree's own cases and recovery paths use — which the daemon's
     wiki-resume lock and its model-server reading both already carry.
  3. A manager that raises while being asked which provider is primary: an
     error, which the same sentence says is answered with a clean note.

Hermetic: no service, no socket, no bus, no model server, no network.  The
single-instance guard is answered False in place, which is what it answers on
a box with no verifiable ownership; nothing is ever sent anywhere, and no case
reaches the consent step.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from intergen.dbus_daemon import InterGenDaemon

_QUESTION = "is my disk encrypted?"


class _ManagerThatRaisesWhenAsked:
    """An escalation step that fails while being asked for its provider."""

    def _primary_provider_name(self):
        raise RuntimeError("provider table unreadable")

    def escalate(self, *a, **k):            # pragma: no cover — never reached
        raise AssertionError("nothing may be sent")


def _a_start_that_stopped_at_the_single_instance_guard() -> InterGenDaemon:
    """A daemon built the way ``ask-frontier --direct`` builds one on a box
    where the guard cannot verify sole ownership: constructed, started, and
    returned from the guard before any subsystem was built."""
    daemon = InterGenDaemon()
    with mock.patch.object(InterGenDaemon, "_claim_bus_name", return_value=False):
        daemon.start_service()
    return daemon


class TheFrontierMethodAnswersInsteadOfRaising(unittest.TestCase):

    def _note(self, daemon) -> dict:
        """The method's reply, read as the JSON note it promises to return."""
        try:
            reply = daemon.escalate(_QUESTION)
        except BaseException as raised:     # noqa: BLE001 — the point of the case
            self.fail(
                "escalate() raised %s(%s) instead of returning the clean JSON "
                "note its own contract promises ('no manager / no provider / "
                "declined consent / error all return a clean JSON note')"
                % (type(raised).__name__, raised))
        self.assertIsInstance(reply, str, "the reply is a JSON string")
        try:
            note = json.loads(reply)
        except ValueError:
            self.fail("escalate() returned something that is not JSON: %r" % reply)
        self.assertIs(note["sent"], False, "nothing was sent")
        self.assertTrue(note["response"].strip(), "the note carries a sentence")
        return note

    def test_escalate_returns_a_not_sent_note_when_the_manager_was_never_built(self):
        """The guard returned before the manager step ran: no manager, so the
        no-manager note — not a traceback out of the guard that reads it."""
        daemon = _a_start_that_stopped_at_the_single_instance_guard()
        self.assertTrue(daemon._duplicate_instance,
                        "this is the start that bound nothing")
        note = self._note(daemon)
        self.assertIn("not available", note["response"].lower(),
                      "the sentence names the missing manager")

    def test_a_partially_constructed_daemon_answers_the_same_way(self):
        """The partial-construction shape this tree already supports elsewhere
        (the wiki-resume lock, the model-server reading) reaches the same
        sentence rather than a missing-attribute failure."""
        daemon = InterGenDaemon.__new__(InterGenDaemon)
        note = self._note(daemon)
        self.assertIn("not available", note["response"].lower(),
                      "the sentence names the missing manager")

    def test_a_manager_that_raises_while_naming_its_provider_is_a_clean_note(self):
        """'error ... returns a clean JSON note': the read that names the
        provider is part of the send path and is covered by that sentence."""
        daemon = InterGenDaemon.__new__(InterGenDaemon)
        daemon._escalation = _ManagerThatRaisesWhenAsked()
        with mock.patch("intergen.consent_modal.prompt_send_consent") as consent:
            note = self._note(daemon)
        consent.assert_not_called()         # nobody was asked; nothing was sent
        self.assertNotEqual(note.get("source"), "frontier",
                            "no provider is named as the source of a note")


if __name__ == "__main__":
    unittest.main()
