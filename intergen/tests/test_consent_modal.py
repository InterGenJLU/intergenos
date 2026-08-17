# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Phone-a-Friend show-before-send consent modal (Sentinel design plan §4).

Verifies the fail-closed semantics without a real GUI: zenity Send -> True, Cancel
-> False, zenity-unavailable -> libnotify fallback (always deny), session-inactive ->
fallback, the outbound content + provider appear in the modal body, and long content
is previewed with a truncation marker. Runs on any host (subprocess is mocked).
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen import consent_modal


def _completed(returncode, stdout=""):
    return mock.Mock(returncode=returncode, stdout=stdout)


class ConsentModalTests(unittest.TestCase):
    def test_zenity_send_returns_true(self):
        with mock.patch.object(consent_modal, "_session_active", return_value=True), \
             mock.patch.object(consent_modal.consent_dialog, "run_consent_dialog",
                               return_value=None), \
             mock.patch.object(consent_modal.shutil, "which", return_value="/usr/bin/zenity"), \
             mock.patch.object(consent_modal.subprocess, "run",
                               return_value=_completed(0)) as run:
            self.assertTrue(consent_modal.prompt_send_consent("hi", "anthropic", "help"))
        # the FULL outbound content + provider go to the scrollable dialog via stdin
        body = run.call_args.kwargs["input"]
        self.assertIn("hi", body)
        self.assertIn("anthropic", body)
        # --text-info (scrollable) so SHOWN == SENT regardless of length
        self.assertIn("--text-info", run.call_args[0][0])

    def test_zenity_cancel_returns_false(self):
        with mock.patch.object(consent_modal, "_session_active", return_value=True), \
             mock.patch.object(consent_modal.consent_dialog, "run_consent_dialog",
                               return_value=None), \
             mock.patch.object(consent_modal.shutil, "which", return_value="/usr/bin/zenity"), \
             mock.patch.object(consent_modal.subprocess, "run",
                               return_value=_completed(1)):
            self.assertFalse(consent_modal.prompt_send_consent("hi", "openai"))

    def test_zenity_oserror_denies(self):
        with mock.patch.object(consent_modal, "_session_active", return_value=True), \
             mock.patch.object(consent_modal.consent_dialog, "run_consent_dialog",
                               return_value=None), \
             mock.patch.object(consent_modal.shutil, "which", return_value="/usr/bin/zenity"), \
             mock.patch.object(consent_modal.subprocess, "run", side_effect=OSError):
            self.assertFalse(consent_modal.prompt_send_consent("hi", "openai"))

    def test_no_zenity_falls_back_and_denies(self):
        # session active but zenity missing -> _prompt_consent_zenity returns None ->
        # libnotify fallback, which always denies (show-before-send can't be honored).
        def which(name):
            return None if name == "zenity" else "/usr/bin/notify-send"
        with mock.patch.object(consent_modal, "_session_active", return_value=True), \
             mock.patch.object(consent_modal.consent_dialog, "run_consent_dialog",
                               return_value=None), \
             mock.patch.object(consent_modal.shutil, "which", side_effect=which), \
             mock.patch.object(consent_modal.subprocess, "run",
                               return_value=_completed(0)):
            self.assertFalse(consent_modal.prompt_send_consent("hi", "openai"))

    def test_session_inactive_falls_back_and_denies(self):
        with mock.patch.object(consent_modal, "_session_active", return_value=False), \
             mock.patch.object(consent_modal.shutil, "which",
                               return_value="/usr/bin/notify-send"), \
             mock.patch.object(consent_modal.subprocess, "run",
                               return_value=_completed(0)):
            self.assertFalse(consent_modal.prompt_send_consent("secret", "google"))

    def test_full_content_shown_no_truncation(self):
        # show-before-send completeness (review note #2): the body contains the FULL
        # payload — SHOWN must equal SENT, so a secret past any old display cap can no
        # longer ride out unseen. A 5000-char payload (past the old 2000 display cap
        # AND the 4096-byte send cap) appears verbatim, with no truncation marker.
        payload = "X" * 5000
        body = consent_modal._format_body(payload, "anthropic", "")
        self.assertIn(payload, body)
        self.assertNotIn("more characters will also be sent", body)
        self.assertNotIn("...", body)

    def test_secret_past_old_cap_is_shown(self):
        # A secret placed past char 2000 (the old display window) but within a
        # realistic payload must now appear in the reviewable body.
        secret = "SECRET-abc123"
        payload = ("Y" * 2500) + secret + ("Z" * 100)
        body = consent_modal._format_body(payload, "openai", "")
        self.assertIn(secret, body)

    def test_default_cancel_flag_present(self):
        # the zenity modal must default to Cancel (fail-closed) — assert the flag.
        with mock.patch.object(consent_modal, "_session_active", return_value=True), \
             mock.patch.object(consent_modal.consent_dialog, "run_consent_dialog",
                               return_value=None), \
             mock.patch.object(consent_modal.shutil, "which", return_value="/usr/bin/zenity"), \
             mock.patch.object(consent_modal.subprocess, "run",
                               return_value=_completed(1)) as run:
            consent_modal.prompt_send_consent("hi", "openai")
        argv = run.call_args[0][0]
        self.assertIn("--default-cancel", argv)
        self.assertIn("--ok-label=Send", argv)


if __name__ == "__main__":
    unittest.main()
