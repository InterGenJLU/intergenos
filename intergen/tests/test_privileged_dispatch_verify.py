# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""AI-6 (option iii) — root-side token-verify + consumed-nonce gate tests.

Proves the root-side canonical-pair behavior in intergen.privileged_dispatch.main():

  1. No request file where the runner said one would be -> refuse.
  1b. A request carrying an empty token -> refuse (a request without an
     approval is a bypass attempt).
  2. A token whose signature does not match this install's key -> refuse.
  3. A token bound to different (tool, args, uid) than dispatched -> refuse.
  4. An expired token -> refuse.
  5. A valid, fresh, single-use token -> the tool executes once.
  6. Replaying the SAME valid token a second time -> refuse (the persistent,
     file-locked consumed-nonce store rejects the spent approval-nonce).

Updated 2026-08-24 for the request-file transport: the token no longer arrives
in an environment variable. It arrives, with the tool name and the arguments,
inside an owner-only request file whose path is main()'s only argument. These
tests write that file the same way the unprivileged side does, so what they
drive is the real contract rather than a convenient one.

main() uses sys.exit() on refusal (via _fail) and returns 0/1 on the execute
path. pkexec/the real registry are mocked; the signing key is injected; the
consumed-nonce store and the runtime directory are both redirected to tempdirs
so no real /var/lib path and no real dispatch state are touched.
"""

from __future__ import annotations

import json
import os
import pwd
import tempfile
import time
import unittest
from unittest import mock

from intergen import dispatch_token as dt
from intergen import privileged_dispatch as pd
from intergen import privileged_request as pr
from intergen.interfaces.types import SafetyTier, ToolResult


_UNSET = object()

_KEY = "ab" * dt.KEY_BYTES
_TOOL = "manage_services"
_ARGS = {"action": "restart", "unit": "sshd"}


class _FakeTool:
    """Minimal stand-in for a discovered privileged tool — passes validation,
    classifies non-BLOCKED, and reports execution so we can assert it ran."""

    def __init__(self, sink):
        self._sink = sink

    def validate_arguments(self, arguments):
        return None

    def classify_safety(self, arguments):
        return SafetyTier.CONFIRM

    def execute(self, arguments):
        self._sink["executed"] = True
        return ToolResult(call_id="", name=_TOOL, content="done", success=True)


class TokenVerifyGateTests(unittest.TestCase):
    def setUp(self):
        self.uid = os.getuid()
        # A REAL, resolvable username — dispatch_key_path_for_user() does a
        # pwd.getpwnam() before the (patched) key load, so a synthetic name
        # would refuse at user-resolution and mask the real verify path.
        self.user = pwd.getpwuid(self.uid).pw_name
        self._sink = {}

        # Redirect the consumed-nonce store to an isolated tempdir.
        self._tmp = tempfile.mkdtemp()
        # And the runtime directory, so request files never land in the real one.
        self._runtime = tempfile.TemporaryDirectory(prefix="privverify-")
        self.addCleanup(self._runtime.cleanup)
        self._runtime_patch = mock.patch.dict(
            os.environ, {"XDG_RUNTIME_DIR": self._runtime.name}, clear=False,
        )
        self._runtime_patch.start()
        self.addCleanup(self._runtime_patch.stop)
        # The token the next _run() will put in the request file.
        self._token = None
        self._dir_patch = mock.patch.object(
            pd, "_CONSUMED_NONCE_DIR", self._tmp,
        )
        self._path_patch = mock.patch.object(
            pd, "_CONSUMED_NONCE_PATH", os.path.join(self._tmp, "consumed-nonces"),
        )
        self._dir_patch.start()
        self._path_patch.start()

        # Inject the signing key on BOTH the mint side (load_dispatch_key) and
        # the root verify side (load_dispatch_key via dispatch_key_path_for_user
        # resolves the same function), so no real key file is read.
        self._key_patch = mock.patch.object(
            dt, "load_dispatch_key", return_value=_KEY,
        )
        self._key_patch.start()

        # Mock the registry so discovery/get_tool/execute are deterministic and
        # the test isolates the verify+nonce gate (not tool discovery).
        self._reg_patch = mock.patch.object(pd, "ToolRegistry")
        reg_cls = self._reg_patch.start()
        reg = reg_cls.return_value
        reg.discover_tools.return_value = 1
        reg.get_tool.return_value = _FakeTool(self._sink)

        # Base environment a correct runner would set.
        self._env = mock.patch.dict(
            os.environ,
            {
                "PKEXEC_UID": str(self.uid),
                "PKEXEC_USER": self.user,
            },
            clear=False,
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._reg_patch.stop()
        self._key_patch.stop()
        self._path_patch.stop()
        self._dir_patch.stop()

    def _set_token(self, token):
        """Stage the token the next dispatch will carry.

        It goes into the request file at _run() time, exactly as the
        unprivileged side would write it.
        """
        self._token = token

    def _mint(self, *, tool=_TOOL, args=None, uid=None, ttl=120, now=None):
        return dt.mint_token(
            tool, args if args is not None else _ARGS,
            uid if uid is not None else self.uid,
            key=_KEY, ttl_seconds=ttl, now=now,
        )

    def _run(self, *, token=_UNSET, tool=_TOOL, args=None):
        """Write a request the way the unprivileged side does, then dispatch it.

        main() may sys.exit() on refusal; the caller captures that as a
        non-zero code.
        """
        payload_token = self._token if token is _UNSET else token
        path = pr.write_request(
            tool, args if args is not None else _ARGS,
            "" if payload_token is None else payload_token,
        )
        return pd.main(["--request", path])

    # --- refusal paths -------------------------------------------------------

    def test_missing_request_refuses(self):
        """The runner named a path; nothing is there. Refuse rather than
        proceed on whatever the caller claimed."""
        missing = os.path.join(self._runtime.name, "no-such-request")
        with self.assertRaises(SystemExit) as cm:
            pd.main(["--request", missing])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("executed", self._sink)

    def test_empty_token_in_the_request_refuses(self):
        """A request without an approval is a bypass attempt, whatever else it
        carries."""
        with self.assertRaises(SystemExit) as cm:
            self._run(token=None)
        self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("executed", self._sink)

    def test_request_owned_by_another_user_refuses(self):
        """Root is handed a path by the unprivileged side. A request that is
        not the calling user's is refused before its contents matter."""
        path = pr.write_request(_TOOL, _ARGS, self._mint())
        with mock.patch.dict(
            os.environ, {"PKEXEC_UID": str(self.uid + 1)}, clear=False,
        ):
            with self.assertRaises(SystemExit) as cm:
                pd.main(["--request", path])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("executed", self._sink)

    def test_wrong_argv_shape_refuses_with_code_two(self):
        with self.assertRaises(SystemExit) as cm:
            pd.main([_TOOL, json.dumps(_ARGS)])
        self.assertEqual(cm.exception.code, 2)
        self.assertNotIn("executed", self._sink)

    def test_the_request_is_removed_by_the_dispatch(self):
        """The approval token does not outlive the dispatch that consumed it."""
        path = pr.write_request(_TOOL, _ARGS, self._mint())
        rc = pd.main(["--request", path])
        self.assertEqual(rc, 0)
        self.assertFalse(
            os.path.exists(path),
            "the request survived the dispatch; an approval token was left on disk",
        )

    def test_bad_signature_refuses(self):
        good = self._mint()
        body, _mac = good.rsplit(".", 1)
        self._set_token(f"{body}.{'0' * len(_mac)}")
        with self.assertRaises(SystemExit) as cm:
            self._run()
        self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("executed", self._sink)

    def test_binding_mismatch_refuses(self):
        # Token minted for a DIFFERENT args set than the request carries.
        self._set_token(self._mint(args={"action": "stop", "unit": "sshd"}))
        with self.assertRaises(SystemExit) as cm:
            self._run()
        self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("executed", self._sink)

    def test_expired_token_refuses(self):
        # Mint already-expired (iat/exp in the past).
        self._set_token(self._mint(ttl=1, now=int(time.time()) - 3600))
        with self.assertRaises(SystemExit) as cm:
            self._run()
        self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("executed", self._sink)

    # --- accept-once + replay ------------------------------------------------

    def test_valid_token_executes_once(self):
        self._set_token(self._mint())
        rc = self._run()
        self.assertEqual(rc, 0)
        self.assertTrue(self._sink.get("executed"))

    def test_replay_same_token_refuses(self):
        token = self._mint()
        self._set_token(token)
        rc = self._run()
        self.assertEqual(rc, 0)
        self.assertTrue(self._sink.get("executed"))
        # Second use of the SAME token (same approval-nonce) must be refused.
        self._sink.clear()
        with self.assertRaises(SystemExit) as cm:
            self._run()
        self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("executed", self._sink)


if __name__ == "__main__":
    unittest.main()
