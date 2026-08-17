# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Chronicle authorization: two tiers, no path leaks, and no client can kill it.

Every verb used to sit behind ONE administrator action, so reading a status
line raised a password dialog — repeatedly, because each client process
authorizes from nothing. Three properties are pinned here.

  1. TIERS. status, queue-status, list and target-scan are served under
     org.intergenos.Chronicle.read, which the policy grants outright to a local
     active session. Everything else — the state-changing verbs and the read
     verbs whose answers carry file paths (manifest, diff, verify,
     restore-plan) — stays behind org.intergenos.Chronicle.manage. A refused
     read is NOT retried against the administrative action, because that would
     put the dialog straight back.

  2. NO PATH LEAK AT THE READ TIER. config.user_data_paths defaults to
     ["/home"], so one shared store holds every user's home directory. The one
     read-tier field that can carry paths is a queued intent's `scope`, and it
     is stripped for read-tier callers while the count and summary — what
     "is my backup healthy" actually needs — survive. A root or manage caller
     still sees the whole object.

  3. ONE CLIENT CANNOT STOP THE ENGINE. The response write is guarded and the
     accept loop guards each connection, because a client that goes away
     mid-response used to raise out of serve() and take the daemon down. A
     backup daemon that a client can crash is worse than a slow one: the
     captures that do not happen while it is down are silent.

The tier tests need a non-root peer, since root skips authorization by design;
they skip with a stated reason under root rather than passing vacuously.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "assets", "intergenos-backup"))

from chronicle import api as _api  # noqa: E402

RUNNING_AS_ROOT = (os.geteuid() == 0)
ROOT_SKIP = ("root peers skip authorization by design; the tiering under test "
             "only applies to a non-root peer")


class _RecordingAuthorizer:
    """Stands in for pkcheck and records every action it was asked about, so a
    test can assert not just the outcome but that the administrative action was
    never consulted — which is what "no password dialog" means here."""

    def __init__(self, grant=()):
        self.grant = set(grant)
        self.calls = []

    def __call__(self, pid, uid, action_id):
        self.calls.append(action_id)
        return action_id in self.grant

    def actions(self):
        return list(self.calls)


class _FakeEngine:
    """Answers the four read verbs with shapes matching the real engine."""

    def __init__(self):
        self.intents = [{
            "id": "0001-abc", "layer": "userdata",
            "scope": {"paths": ["/home/otheruser/taxes.pdf",
                                "/home/otheruser/.ssh/id_ed25519"]},
            "trigger_time": 100, "reason": "userdata change", "estimate": 4096,
        }]

    def queue_status(self):
        return {"count": 1, "summary": "1 change queued.",
                "intents": self.intents}

    def status(self):
        return {"target": {"mountpoint": "/run/media/backup"},
                "target_present": True, "target_free_bytes": 1 << 30,
                "last_capture": {"wall_clock": 99},
                "queue": self.queue_status(),
                "clock_skew_events": [], "pins": []}

    def list_versions(self, layer, since=None, until=None):
        return [{"version_id": "v1", "layer": layer, "sequence": 1,
                 "wall_clock": 99, "reason": "pre-transaction install: nginx",
                 "pinned": False, "files": 12}]

    def target_scan(self, home_estimate_bytes=None, floor_bytes=None):
        return [{"device": "/dev/sdb1", "size_bytes": 1 << 40}]


class TierClassificationTests(unittest.TestCase):

    def setUp(self):
        self._orig = _api._PEER_AUTHORIZER
        self.uid = os.getuid()

    def tearDown(self):
        _api._PEER_AUTHORIZER = self._orig

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_read_verbs_use_only_the_read_action(self):
        for verb in sorted(_api.READ_ONLY_VERBS):
            auth = _RecordingAuthorizer(grant={_api.POLKIT_ACTION_READ})
            _api._PEER_AUTHORIZER = auth
            ok, tier, _r = _api.authorize_verb(1234, self.uid, verb)
            self.assertTrue(ok, verb)
            self.assertEqual(tier, "read", verb)
            self.assertEqual(auth.actions(), [_api.POLKIT_ACTION_READ], verb)
            self.assertNotIn(_api.POLKIT_ACTION_ID, auth.actions(), verb)

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_a_refused_read_is_not_escalated_to_the_admin_action(self):
        # The whole point: a denied read must NOT fall back to asking for a
        # password, or the dialog this change removes comes straight back.
        auth = _RecordingAuthorizer(grant=set())
        _api._PEER_AUTHORIZER = auth
        ok, tier, reason = _api.authorize_verb(1234, self.uid, "status")
        self.assertFalse(ok)
        self.assertIsNone(tier)
        self.assertEqual(auth.actions(), [_api.POLKIT_ACTION_READ])
        self.assertIn(_api.POLKIT_ACTION_READ, reason)

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_path_bearing_read_verbs_stay_administrative(self):
        # These only read, but their answers carry other users' paths.
        for verb in ("manifest", "diff", "verify", "restore-plan"):
            self.assertNotIn(verb, _api.READ_ONLY_VERBS, verb)
            auth = _RecordingAuthorizer(grant={_api.POLKIT_ACTION_READ})
            _api._PEER_AUTHORIZER = auth
            ok, _tier, _r = _api.authorize_verb(1234, self.uid, verb)
            self.assertFalse(ok, f"{verb} must not be served by the read tier")
            self.assertEqual(auth.actions(), [_api.POLKIT_ACTION_ID], verb)

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_state_changing_verbs_stay_administrative(self):
        for verb in ("capture", "restore", "scrub", "retention-apply",
                     "pin", "unpin", "target-adopt"):
            self.assertNotIn(verb, _api.READ_ONLY_VERBS, verb)
            auth = _RecordingAuthorizer(grant={_api.POLKIT_ACTION_ID})
            _api._PEER_AUTHORIZER = auth
            ok, tier, _r = _api.authorize_verb(1234, self.uid, verb)
            self.assertTrue(ok, verb)
            self.assertEqual(tier, "manage", verb)

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_unknown_verb_is_refused_without_consulting_polkit(self):
        # An unknown verb must never be a way to make a dialog appear.
        auth = _RecordingAuthorizer(grant={_api.POLKIT_ACTION_ID,
                                           _api.POLKIT_ACTION_READ})
        _api._PEER_AUTHORIZER = auth
        ok, tier, reason = _api.authorize_verb(1234, self.uid, "rm -rf")
        self.assertFalse(ok)
        self.assertIsNone(tier)
        self.assertEqual(auth.actions(), [])
        self.assertIn("unknown verb", reason)

    def test_every_verb_is_classified(self):
        # No verb may be silently absent from both tiers.
        self.assertTrue(_api.READ_ONLY_VERBS <= set(_api._VERBS))

    def test_the_read_set_is_exactly_this(self):
        # Pinned as a literal on purpose. Emptying READ_ONLY_VERBS would make
        # every loop that iterates over it pass vacuously — the mutation run
        # for this change proved that, so the membership is asserted directly
        # rather than only exercised. Changing this set is a deliberate act and
        # must fail here first, in both directions: a verb added to the read
        # tier is a privacy decision, and a verb removed is a password put back
        # in front of the user.
        self.assertEqual(
            set(_api.READ_ONLY_VERBS),
            {"status", "queue-status", "list", "target-scan"})


class ReadTierRedactionTests(unittest.TestCase):
    """The read tier must not hand out another user's file paths."""

    def setUp(self):
        self.engine = _FakeEngine()

    def _leaks_a_path(self, payload):
        return "/home/otheruser" in json.dumps(payload)

    def test_status_at_read_tier_drops_intent_scope(self):
        full = _api.dispatch(self.engine, {"verb": "status", "args": {}})
        self.assertTrue(self._leaks_a_path(full),
                        "precondition: the unredacted status carries paths")
        redacted = _api._redact_for_read("status", full["result"])
        self.assertFalse(self._leaks_a_path(redacted))
        # The health information a user actually needs survives.
        self.assertEqual(redacted["queue"]["count"], 1)
        self.assertEqual(redacted["queue"]["summary"], "1 change queued.")
        self.assertEqual(redacted["target_free_bytes"], 1 << 30)
        self.assertTrue(redacted["target_present"])
        self.assertEqual(len(redacted["queue"]["intents"]), 1)
        self.assertNotIn("scope", redacted["queue"]["intents"][0])
        self.assertEqual(redacted["queue"]["intents"][0]["id"], "0001-abc")

    def test_queue_status_at_read_tier_drops_intent_scope(self):
        full = _api.dispatch(self.engine, {"verb": "queue-status", "args": {}})
        self.assertTrue(self._leaks_a_path(full))
        redacted = _api._redact_for_read("queue-status", full["result"])
        self.assertFalse(self._leaks_a_path(redacted))
        self.assertEqual(redacted["count"], 1)

    def test_redaction_does_not_mutate_the_engine_state(self):
        # A root caller in the same process must still see everything.
        full = _api.dispatch(self.engine, {"verb": "status", "args": {}})
        _api._redact_for_read("status", full["result"])
        again = _api.dispatch(self.engine, {"verb": "status", "args": {}})
        self.assertTrue(self._leaks_a_path(again),
                        "redaction must copy, never edit the engine's objects")
        self.assertIn("scope", self.engine.intents[0])

    def test_list_and_target_scan_carry_no_paths_to_redact(self):
        for verb, args in (("list", {"layer": "userdata"}),
                           ("target-scan", {})):
            resp = _api.dispatch(self.engine, {"verb": verb, "args": args})
            self.assertTrue(resp["ok"], verb)
            self.assertFalse(self._leaks_a_path(resp), verb)


class ConnectionRobustnessTests(unittest.TestCase):
    """A client must not be able to take the engine down."""

    def setUp(self):
        self._orig = _api._PEER_AUTHORIZER
        _api._PEER_AUTHORIZER = _RecordingAuthorizer(
            grant={_api.POLKIT_ACTION_READ, _api.POLKIT_ACTION_ID})

    def tearDown(self):
        _api._PEER_AUTHORIZER = self._orig

    def test_peer_gone_before_the_response_does_not_raise(self):
        # This is the crash observed in the field: the success-path write had
        # no guard, so a client that gave up killed the daemon.
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        a.sendall(b'{"verb": "status", "args": {}}\n')
        a.close()          # the client vanishes before reading the answer
        try:
            _api._handle_conn(_FakeEngine(), b)   # must not raise
        finally:
            b.close()

    def test_send_swallows_a_dead_peer(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        a.close()
        try:
            _api._send(b, {"ok": True, "result": None})   # must not raise
        finally:
            b.close()

    def test_malformed_request_is_answered_not_fatal(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        a.sendall(b'this is not json\n')
        try:
            _api._handle_conn(_FakeEngine(), b)
            reply = json.loads(a.recv(65536).decode("utf-8"))
        finally:
            a.close(); b.close()
        self.assertFalse(reply["ok"])
        self.assertIn("malformed request", reply["error"])

    def test_a_json_scalar_is_rejected_as_a_request(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        a.sendall(b'"just a string"\n')
        try:
            _api._handle_conn(_FakeEngine(), b)
            reply = json.loads(a.recv(65536).decode("utf-8"))
        finally:
            a.close(); b.close()
        self.assertFalse(reply["ok"])


class OneAuthorizationPerOperationTests(unittest.TestCase):
    """Item 3: the repetition is one authorization per operation, not a retry
    loop. Nothing in the daemon may ask twice for one request."""

    def setUp(self):
        self._orig = _api._PEER_AUTHORIZER

    def tearDown(self):
        _api._PEER_AUTHORIZER = self._orig

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_one_request_causes_exactly_one_authorization(self):
        auth = _RecordingAuthorizer(grant={_api.POLKIT_ACTION_READ,
                                           _api.POLKIT_ACTION_ID})
        _api._PEER_AUTHORIZER = auth
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        a.sendall(b'{"verb": "status", "args": {}}\n')
        try:
            _api._handle_conn(_FakeEngine(), b)
            a.recv(65536)
        finally:
            a.close(); b.close()
        self.assertEqual(len(auth.actions()), 1,
                         f"expected one authorization, got {auth.actions()}")

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_ten_status_reads_never_touch_the_admin_action(self):
        # Before the split, ten status reads meant ten administrator dialogs.
        auth = _RecordingAuthorizer(grant={_api.POLKIT_ACTION_READ})
        _api._PEER_AUTHORIZER = auth
        for _ in range(10):
            ok, tier, _r = _api.authorize_verb(1234, os.getuid(), "status")
            self.assertTrue(ok)
            self.assertEqual(tier, "read")
        self.assertEqual(auth.actions(), [_api.POLKIT_ACTION_READ] * 10)
        self.assertNotIn(_api.POLKIT_ACTION_ID, auth.actions())


if __name__ == "__main__":
    unittest.main()
