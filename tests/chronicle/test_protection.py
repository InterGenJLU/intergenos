#!/usr/bin/env python3
"""The protection-status state model: six-state classification, the copy
table, and the lock between the engine's authorization-refused reason string
and the GUI-side detection marker (decided 2026-07-30; NO_ACCESS added
2026-08-04 with the engine socket's group gate)."""

import os
import socket
import unittest

from chronicle import api as _api
from chronicle import protection as _protection


class ClassifyTest(unittest.TestCase):
    def test_captures_with_attached_target_is_protected(self):
        st = {"target": {"mountpoint": "/run/media/backup"},
              "target_present": True,
              "last_capture": {"user-data": 1000}}
        self.assertEqual(_protection.classify(st), _protection.PROTECTED)

    def test_no_external_target_is_a_supported_configuration(self):
        # The always-on local history still runs: capture history alone
        # decides, and a target-less payload with captures is PROTECTED.
        st = {"target": None, "last_capture": {"config-state": 1000}}
        self.assertEqual(_protection.classify(st), _protection.PROTECTED)

    def test_nothing_captured_yet(self):
        st = {"target": {"mountpoint": "/x"}, "target_present": True,
              "last_capture": {}}
        self.assertEqual(_protection.classify(st), _protection.NO_CAPTURES)

    def test_no_target_and_no_captures(self):
        self.assertEqual(_protection.classify({"target": None,
                                               "last_capture": {}}),
                         _protection.NO_CAPTURES)

    def test_configured_but_absent_target_outranks_capture_history(self):
        # Captures exist, but they cannot currently reach the target.
        st = {"target": {"mountpoint": "/x"}, "target_present": False,
              "last_capture": {"user-data": 1000}}
        self.assertEqual(_protection.classify(st), _protection.TARGET_ABSENT)

    def test_latest_capture_epoch_is_the_newest_layer(self):
        st = {"last_capture": {"a": 100, "b": 300, "c": 200}}
        self.assertEqual(_protection.latest_capture_epoch(st), 300)
        self.assertIsNone(_protection.latest_capture_epoch({"last_capture": {}}))


class CopyTableTest(unittest.TestCase):
    """The copy table ships exactly the decided strings — wording drifts are
    a test failure, not a restyle."""

    EXPECTED = {
        "header.name": "Chronicle",
        "verdict.protected": "Protected — last capture {when}",
        "verdict.no_captures": "Not protected yet — nothing has been captured",
        "verdict.target_absent": "Paused — the backup drive is not attached",
        "verdict.service_down":
            "Backups are paused — the Chronicle service isn't running",
        "verdict.unauthorized":
            "Waiting for permission — Chronicle needs an administrator to "
            "allow backups",
        "verdict.no_access":
            "This account cannot use Chronicle — it is not allowed to reach "
            "the backup service",
        "banner.service_down":
            "Backups are paused — the Chronicle service isn't running.",
        "banner.no_access":
            "This account cannot use Chronicle. Backups are still running; "
            "this account is not allowed to see or change them.",
        "banner.button": "Start",
        "card.meaning": "Nothing is being captured right now",
        "card.existing": "Safe — restoring needs the service running too",
        "action.start": "Start backups",
        "action.capture": "Capture now",
        "action.choose_drive": "Choose a drive",
        "action.allow": "Allow…",
        "expander.technical": "Technical details",
        "tooltip.capture_disabled":
            "The Chronicle service must be running to capture",
        "no_access.remedy":
            "An administrator can allow it by adding this account to the "
            "\"chronicle\" group. The change takes effect at the next login.",
    }

    def test_copy_table_is_exactly_the_decided_strings(self):
        self.assertEqual(_protection.COPY, self.EXPECTED)

    def test_every_state_has_verdict_tone_and_tag(self):
        states = {_protection.PROTECTED, _protection.NO_CAPTURES,
                  _protection.TARGET_ABSENT, _protection.SERVICE_DOWN,
                  _protection.UNAUTHORIZED, _protection.NO_ACCESS}
        self.assertEqual(set(_protection.VERDICT_KEY), states)
        self.assertEqual(set(_protection.TONE), states)
        self.assertEqual(set(_protection.TAG), states)
        for key in _protection.VERDICT_KEY.values():
            self.assertIn(key, _protection.COPY)
        # Tones are the theme's semantic classes, nothing invented.
        self.assertTrue(set(_protection.TONE.values())
                        <= {"success", "warning", "error"})

    def test_verdicts_never_name_the_daemon(self):
        # The verdict names the user's situation, never a component; the
        # unit name lives only behind the Technical-details expander.
        for key, text in _protection.COPY.items():
            if key.startswith(("verdict.", "banner.", "card.")):
                self.assertNotIn("chronicled", text, key)
                self.assertNotIn("systemctl", text, key)

    def test_start_argv_is_fixed_and_shell_free(self):
        self.assertEqual(_protection.START_ARGV,
                         ["systemctl", "start", "chronicled.service"])

    def test_the_group_name_matches_the_engine(self):
        # The copy tells the user which group to be added to; the engine
        # decides which group may open the socket. One typo apart and the
        # instruction sends an administrator to the wrong group.
        self.assertEqual(_protection.ENGINE_GROUP, _api.ENGINE_SOCKET_GROUP)
        self.assertIn(_protection.ENGINE_GROUP,
                      _protection.COPY["no_access.remedy"])


class UnauthorizedMarkerTest(unittest.TestCase):
    """protection.is_unauthorized must recognize exactly the reason string
    the engine's peer authorization emits — locked against the real
    authorize_verb so neither side can drift without the other."""

    def test_marker_matches_the_engine_reason(self):
        if os.getuid() == 0:
            self.skipTest("root peers bypass authorization")
        old = _api._PEER_AUTHORIZER
        _api._PEER_AUTHORIZER = lambda pid, uid, action: False
        try:
            ok, _tier, reason = _api.authorize_verb(1234, os.getuid(), "restore")
        finally:
            _api._PEER_AUTHORIZER = old
        self.assertFalse(ok)
        self.assertTrue(_protection.is_unauthorized(reason), reason)

    def test_other_errors_are_not_unauthorized(self):
        self.assertFalse(_protection.is_unauthorized("engine error"))
        self.assertFalse(_protection.is_unauthorized(""))
        self.assertFalse(_protection.is_unauthorized(None))


if __name__ == "__main__":
    unittest.main()
