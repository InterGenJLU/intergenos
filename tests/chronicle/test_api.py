#!/usr/bin/env python3
"""The engine IPC surface: dispatch verb/method parity, and the per-connection
polkit peer authorization (root bypass, non-root fail-closed)."""

import os
import socket
import tempfile
import unittest

from chronicle import api as _api
from chronicle import engine as _engine


class DispatchParityTest(unittest.TestCase):
    def test_every_verb_maps_to_a_real_engine_method(self):
        for verb, (method_name, _args) in _api._VERBS.items():
            self.assertTrue(hasattr(_engine.Engine, method_name),
                            f"verb {verb!r} maps to missing Engine.{method_name}")

    def test_unknown_verb_is_a_clean_error(self):
        tmp = tempfile.mkdtemp(prefix="chronicle-api-")
        eng = _engine.Engine(local_root=tmp)
        resp = _api.dispatch(eng, {"verb": "no-such-verb"})
        self.assertFalse(resp["ok"])
        self.assertIn("unknown verb", resp["error"])
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    def test_dispatch_never_raises_on_engine_error(self):
        tmp = tempfile.mkdtemp(prefix="chronicle-api-")
        eng = _engine.Engine(local_root=tmp)
        # capture with an unknown layer -> EngineError -> ok:false, not a raise.
        resp = _api.dispatch(eng, {"verb": "capture", "args": {"layer": "bogus"}})
        self.assertFalse(resp["ok"])
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


class AuthorizePeerTest(unittest.TestCase):
    """Peer authorization. authorize_peer(conn) was replaced by
    authorize_verb(pid, uid, verb) when the single administrator action was
    split in two: which authorization is required now depends on the verb, so
    it cannot be decided from the connection alone."""

    def setUp(self):
        self._orig = _api._PEER_AUTHORIZER
        self._uid = os.getuid()

    def tearDown(self):
        _api._PEER_AUTHORIZER = self._orig

    def test_non_root_denied_when_authorizer_refuses(self):
        if self._uid == 0:
            self.skipTest("root peers bypass authorization by design")
        _api._PEER_AUTHORIZER = lambda pid, uid, action: False
        ok, tier, reason = _api.authorize_verb(1234, self._uid, "restore")
        self.assertFalse(ok)
        self.assertIsNone(tier)
        self.assertIn("not authorized", reason)

    def test_non_root_allowed_when_authorizer_permits(self):
        if self._uid == 0:
            self.skipTest("root peers bypass authorization by design")
        _api._PEER_AUTHORIZER = lambda pid, uid, action: True
        ok, tier, _reason = _api.authorize_verb(1234, self._uid, "restore")
        self.assertTrue(ok)
        self.assertEqual(tier, "manage")

    def test_root_peer_skips_the_check_entirely(self):
        def _never(pid, uid, action):
            raise AssertionError("root must not reach the authorizer")
        _api._PEER_AUTHORIZER = _never
        ok, tier, _reason = _api.authorize_verb(1234, 0, "restore")
        self.assertTrue(ok)
        self.assertEqual(tier, "root")

    def test_pkcheck_is_fail_closed_on_bad_pid(self):
        # A pid that cannot be resolved (/proc read fails) must deny, never
        # fail open. Uses a pid unlikely to exist.
        # Both tiers must fail closed on an unresolvable pid, not just one.
        for action in (_api.POLKIT_ACTION_ID, _api.POLKIT_ACTION_READ):
            self.assertFalse(
                _api._pkcheck_authorized(pid=2 ** 31 - 1, uid=1000,
                                         action_id=action),
                f"{action} must deny when /proc cannot be read")


if __name__ == "__main__":
    unittest.main()
