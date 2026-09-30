# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""THE FALLBACK CONSENT DIALOG CANNOT HOLD THE THREAD FOR EVER.

THE DEFECT THIS CLOSES. The branded consent dialog carries an absolute
deadline, and the comment on that constant says the branded path and the
fallback expire identically. They did not. The fallback's call to the dialog
program was made with no deadline at all, so a program that never returned —
wedged, stopped, waiting on a display that will never answer — held the calling
thread for as long as the process lived. Nothing was sent, which is the safe
half; the unsafe half is that nothing was recorded either, and the thread that
was going to answer the person never came back.

WHAT IT DOES NOW. The call carries the branded dialog's own constant, read from
where that dialog reads it rather than restated here, so the two cannot drift
apart. At the deadline the program is killed and the outcome is the one for a
send nobody was asked about — never the person's decline, and never a send.
That distinction is the point: a person who was shown the content and said
nothing has not cancelled, and telling them they cancelled would record a
refusal the product made on its own behalf as theirs.

HOW THESE CASES POSE IT. With a real program that never returns, driven through
the product's own fallback path, and with the deadline shortened for the run so
the case finishes in a second rather than an hour. The product call is made on a
worker thread and the case waits a bounded time for it: at the parent of this
commit that thread never comes back, which is the defect; here it comes back at
the deadline. Every stand-in program writes its pid, and each case kills
anything still alive in a finally, so no case can leave a process behind.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from intergen import consent_dialog_proto, consent_modal

_WEDGE = """import os, sys, time
open(sys.argv[-1], "w").write(str(os.getpid()))
sys.stdin.read()
time.sleep(3600)
"""

_PROMPT = """import os, sys
open(sys.argv[-1], "w").write(str(os.getpid()))
sys.stdin.read()
sys.exit(int(os.environ["STAND_IN_EXIT"]))
"""


class _StandIn:
    """A dialog program of this case's own, and its pid file."""

    def __init__(self, source):
        self.dir = tempfile.mkdtemp(prefix="consent-deadline-")
        self.pidfile = Path(self.dir) / "pid"
        script = Path(self.dir) / "stand-in-dialog.py"
        script.write_text(source)
        launcher = Path(self.dir) / "stand-in-dialog"
        launcher.write_text(
            f"#!/bin/sh\nexec {sys.executable} {script} \"$@\" {self.pidfile}\n")
        launcher.chmod(0o755)
        self.path = str(launcher)

    def pid(self):
        for _ in range(100):
            if self.pidfile.exists() and self.pidfile.read_text().strip():
                return int(self.pidfile.read_text().strip())
            time.sleep(0.05)
        return None

    def alive(self):
        pid = self.pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def stop(self):
        pid = self.pid()
        if pid is None:
            return
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


class _Asked:
    """The product's fallback path, driven on a worker thread."""

    def __init__(self, program):
        self.program = program
        self.outcome = []
        self.allowed = None
        self.returned_after = None
        self.thread = None

    def start(self):
        def run():
            began = time.monotonic()
            with mock.patch.object(consent_modal, "_session_active",
                                   return_value=True), \
                 mock.patch.object(consent_modal.consent_dialog,
                                   "run_consent_dialog", return_value=None), \
                 mock.patch.object(consent_modal.shutil, "which",
                                   return_value=self.program), \
                 mock.patch.object(consent_modal, "_prompt_consent_libnotify",
                                   return_value=False):
                self.allowed = consent_modal.prompt_send_consent(
                    "PAYLOAD-197", "example-provider", outcome=self.outcome)
            self.returned_after = time.monotonic() - began

        self.thread = threading.Thread(target=run, daemon=True,
                                       name="consent-deadline-wait")
        self.thread.start()
        return self

    def wait(self, seconds):
        self.thread.join(seconds)
        return not self.thread.is_alive()


class TheFallbackDialogHasADeadline(unittest.TestCase):
    def test_a_program_that_never_returns_is_killed_and_nobody_was_asked(self):
        stand_in = _StandIn(_WEDGE)
        asked = None
        with mock.patch.object(consent_dialog_proto,
                               "POST_RENDER_DEADLINE_SECONDS", 1.0):
            try:
                asked = _Asked(stand_in.path).start()
                self.assertIsNotNone(stand_in.pid(), "the stand-in never started")
                came_back = asked.wait(20)
                self.assertTrue(came_back, (
                    "the call never returned: the fallback dialog holds the "
                    "calling thread for as long as the program lives"))
                self.assertFalse(asked.allowed, "nothing may be sent")
                self.assertEqual(asked.outcome, [consent_modal.NOT_SHOWN], (
                    "a person who was shown the content and said nothing has "
                    "not cancelled; the record must not say they did"))
                self.assertEqual(consent_modal.refusal_sentence(asked.outcome),
                                 consent_modal.NOT_SHOWN_SENTENCE)
                self.assertLess(asked.returned_after, 15.0,
                                "the deadline did not bound the wait")
                self.assertFalse(stand_in.alive(),
                                 "the dialog program was left running")
            finally:
                stand_in.stop()

    def test_the_deadline_is_the_branded_dialogs_own_constant(self):
        # Read from the one place that holds it, so the two paths cannot drift.
        with mock.patch.object(consent_modal, "_session_active", return_value=True), \
             mock.patch.object(consent_modal.consent_dialog, "run_consent_dialog",
                               return_value=None), \
             mock.patch.object(consent_modal.shutil, "which",
                               return_value="/usr/bin/zenity"), \
             mock.patch.object(consent_modal.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stdout="",
                                                      stderr="")) as run:
            consent_modal.prompt_send_consent("hi", "openai")
        self.assertEqual(run.call_args.kwargs.get("timeout"),
                         consent_dialog_proto.POST_RENDER_DEADLINE_SECONDS, (
                             "the fallback call carries no deadline, or one of "
                             "its own that can drift from the branded path's"))

    def test_a_program_that_answers_in_time_is_not_killed_or_recorded_as_unshown(self):
        """CONTROL: the ordinary decline still reads as the person's own."""
        stand_in = _StandIn(_PROMPT)
        with mock.patch.dict(os.environ, {"STAND_IN_EXIT": "1"}), \
             mock.patch.object(consent_dialog_proto,
                               "POST_RENDER_DEADLINE_SECONDS", 30.0):
            try:
                asked = _Asked(stand_in.path).start()
                self.assertTrue(asked.wait(20), "the stand-in never answered")
                self.assertFalse(asked.allowed)
                self.assertEqual(asked.outcome, [consent_modal.DECLINED])
                self.assertEqual(consent_modal.refusal_sentence(asked.outcome),
                                 consent_modal.DECLINED_SENTENCE)
            finally:
                stand_in.stop()

    def test_a_program_that_says_send_in_time_still_sends(self):
        """CONTROL: the deadline did not close the path it is there to bound."""
        stand_in = _StandIn(_PROMPT)
        with mock.patch.dict(os.environ, {"STAND_IN_EXIT": "0"}), \
             mock.patch.object(consent_dialog_proto,
                               "POST_RENDER_DEADLINE_SECONDS", 30.0):
            try:
                asked = _Asked(stand_in.path).start()
                self.assertTrue(asked.wait(20), "the stand-in never answered")
                self.assertTrue(asked.allowed)
                self.assertEqual(asked.outcome, [consent_modal.SEND])
            finally:
                stand_in.stop()

    def test_the_shipped_deadline_is_an_hour(self):
        """CONTROL: the constant this reads is the one-hour implicit deny, so a
        run of these cases cannot pass by shortening the shipped value."""
        self.assertEqual(consent_dialog_proto.POST_RENDER_DEADLINE_SECONDS,
                         3600.0)


if __name__ == "__main__":
    unittest.main()
