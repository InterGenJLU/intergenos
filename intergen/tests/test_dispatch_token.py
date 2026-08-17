# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for the AI-6 dispatch-token key infrastructure (mint + verify).

Covers the full failure taxonomy the root-side verify relies on: a token must
verify ONLY when it was minted by this install's key AND binds to exactly the
(tool, args, uid) root received AND is fresh. Every other case fails closed with
a specific exception. Also covers key-management security: 0o600 gen-on-first-run,
idempotent ensure, and rejection of insecure-perm / malformed keys.

Runs on any host: stdlib-only, no display, no gi, no network.
"""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from intergen import dispatch_token as dt


class DispatchTokenTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.key_path = self.tmp / ".config" / "intergen" / "dispatch-key"
        self.tool = "manage_services"
        self.args = {"action": "restart", "unit": "sshd"}
        self.uid = os.getuid()

    def tearDown(self):
        self._tmp.cleanup()


class RoundTripTests(DispatchTokenTestBase):
    def test_valid_token_verifies_and_returns_payload(self):
        key = dt.generate_dispatch_key(self.key_path)
        token = dt.mint_token(self.tool, self.args, self.uid, key=key)
        payload = dt.verify_token(token, self.tool, self.args, self.uid, key=key)
        self.assertEqual(payload.tool, self.tool)
        self.assertEqual(payload.uid, self.uid)
        self.assertEqual(payload.args_sha256, dt.args_digest(self.args))
        self.assertEqual(payload.version, dt.TOKEN_VERSION)
        self.assertTrue(payload.nonce)
        self.assertEqual(payload.exp - payload.iat, dt.DEFAULT_TTL_SECONDS)

    def test_verify_returns_nonce_for_replay_store(self):
        # The root side reads payload.nonce to enforce single-use; it must be the
        # exact nonce minted.
        key = dt.generate_dispatch_key(self.key_path)
        token = dt.mint_token(self.tool, self.args, self.uid, key=key, nonce="deadbeef" * 4)
        payload = dt.verify_token(token, self.tool, self.args, self.uid, key=key)
        self.assertEqual(payload.nonce, "deadbeef" * 4)

    def test_nonces_are_unique_across_mints(self):
        key = dt.generate_dispatch_key(self.key_path)
        nonces = set()
        for _ in range(50):
            token = dt.mint_token(self.tool, self.args, self.uid, key=key)
            payload = dt.verify_token(token, self.tool, self.args, self.uid, key=key)
            nonces.add(payload.nonce)
        self.assertEqual(len(nonces), 50)


class BindingTests(DispatchTokenTestBase):
    def test_wrong_tool_rejected(self):
        key = dt.generate_dispatch_key(self.key_path)
        token = dt.mint_token(self.tool, self.args, self.uid, key=key)
        with self.assertRaises(dt.BindingMismatch):
            dt.verify_token(token, "run_command", self.args, self.uid, key=key)

    def test_wrong_args_rejected(self):
        key = dt.generate_dispatch_key(self.key_path)
        token = dt.mint_token(self.tool, self.args, self.uid, key=key)
        with self.assertRaises(dt.BindingMismatch):
            dt.verify_token(token, self.tool, {"action": "stop", "unit": "sshd"},
                            self.uid, key=key)

    def test_wrong_uid_rejected(self):
        key = dt.generate_dispatch_key(self.key_path)
        token = dt.mint_token(self.tool, self.args, self.uid, key=key)
        with self.assertRaises(dt.BindingMismatch):
            dt.verify_token(token, self.tool, self.args, self.uid + 1, key=key)

    def test_args_digest_is_key_order_independent(self):
        # Canonicalization means root recomputing the digest over a dict built in
        # a different insertion order still matches.
        a = {"unit": "sshd", "action": "restart"}
        b = {"action": "restart", "unit": "sshd"}
        self.assertEqual(dt.args_digest(a), dt.args_digest(b))
        key = dt.generate_dispatch_key(self.key_path)
        token = dt.mint_token(self.tool, a, self.uid, key=key)
        # Verifies against the same args in the other order.
        dt.verify_token(token, self.tool, b, self.uid, key=key)


class SignatureTests(DispatchTokenTestBase):
    def test_tampered_mac_rejected(self):
        key = dt.generate_dispatch_key(self.key_path)
        token = dt.mint_token(self.tool, self.args, self.uid, key=key)
        body, mac = token.rsplit(".", 1)
        flipped = ("0" if mac[-1] != "0" else "1")
        with self.assertRaises(dt.BadSignature):
            dt.verify_token(body + "." + mac[:-1] + flipped, self.tool, self.args,
                            self.uid, key=key)

    def test_tampered_body_rejected(self):
        # Flip a char in the body — mac no longer matches the transmitted body.
        key = dt.generate_dispatch_key(self.key_path)
        token = dt.mint_token(self.tool, self.args, self.uid, key=key)
        body, mac = token.rsplit(".", 1)
        tampered_body = ("A" if body[0] != "A" else "B") + body[1:]
        with self.assertRaises(dt.BadSignature):
            dt.verify_token(tampered_body + "." + mac, self.tool, self.args,
                            self.uid, key=key)

    def test_wrong_key_rejected(self):
        key = dt.generate_dispatch_key(self.key_path)
        token = dt.mint_token(self.tool, self.args, self.uid, key=key)
        other_key = "ab" * dt.KEY_BYTES
        with self.assertRaises(dt.BadSignature):
            dt.verify_token(token, self.tool, self.args, self.uid, key=other_key)


class MalformedTokenTests(DispatchTokenTestBase):
    def test_no_separator_rejected(self):
        key = dt.generate_dispatch_key(self.key_path)
        with self.assertRaises(dt.MalformedToken):
            dt.verify_token("not-a-token", self.tool, self.args, self.uid, key=key)

    def test_empty_segments_rejected(self):
        key = dt.generate_dispatch_key(self.key_path)
        with self.assertRaises(dt.MalformedToken):
            dt.verify_token("body.", self.tool, self.args, self.uid, key=key)
        with self.assertRaises(dt.MalformedToken):
            dt.verify_token(".mac", self.tool, self.args, self.uid, key=key)

    def test_wrong_version_rejected(self):
        # Hand-craft a correctly-signed token with an unsupported version: the
        # signature passes but the version gate must still fail closed.
        key = dt.generate_dispatch_key(self.key_path)
        payload = {
            "v": 99, "tool": self.tool, "args_sha256": dt.args_digest(self.args),
            "nonce": "00" * 16, "uid": self.uid, "iat": 1000, "exp": 9999999999,
        }
        body = dt._b64url_encode(dt._canonical_json(payload).encode())
        token = body + "." + dt._sign(key, body)
        with self.assertRaises(dt.MalformedToken):
            dt.verify_token(token, self.tool, self.args, self.uid, key=key)


class FreshnessTests(DispatchTokenTestBase):
    def test_expired_token_rejected(self):
        key = dt.generate_dispatch_key(self.key_path)
        token = dt.mint_token(self.tool, self.args, self.uid, key=key, now=1000)
        # now is well past exp (1000 + DEFAULT_TTL).
        with self.assertRaises(dt.TokenExpired):
            dt.verify_token(token, self.tool, self.args, self.uid, key=key,
                            now=1000 + dt.DEFAULT_TTL_SECONDS + 1)

    def test_not_yet_valid_rejected(self):
        key = dt.generate_dispatch_key(self.key_path)
        token = dt.mint_token(self.tool, self.args, self.uid, key=key, now=10_000)
        with self.assertRaises(dt.TokenExpired):
            dt.verify_token(token, self.tool, self.args, self.uid, key=key, now=9_000)

    def test_within_ttl_accepted(self):
        key = dt.generate_dispatch_key(self.key_path)
        token = dt.mint_token(self.tool, self.args, self.uid, key=key, now=10_000)
        dt.verify_token(token, self.tool, self.args, self.uid, key=key,
                        now=10_000 + dt.DEFAULT_TTL_SECONDS - 1)


class KeyManagementTests(DispatchTokenTestBase):
    def test_generate_creates_0600(self):
        dt.generate_dispatch_key(self.key_path)
        mode = stat.S_IMODE(self.key_path.stat().st_mode)
        self.assertEqual(mode, dt.KEY_MODE)

    def test_generated_key_shape(self):
        key = dt.generate_dispatch_key(self.key_path)
        self.assertEqual(len(key), dt.KEY_BYTES * 2)
        int(key, 16)  # raises if not hex

    def test_ensure_generates_on_first_run(self):
        self.assertFalse(self.key_path.exists())
        key = dt.ensure_dispatch_key(self.key_path)
        self.assertTrue(self.key_path.exists())
        self.assertEqual(len(key), dt.KEY_BYTES * 2)

    def test_ensure_is_idempotent_and_does_not_clobber(self):
        first = dt.ensure_dispatch_key(self.key_path)
        second = dt.ensure_dispatch_key(self.key_path)
        self.assertEqual(first, second)

    def test_load_rejects_insecure_permissions(self):
        dt.generate_dispatch_key(self.key_path)
        os.chmod(self.key_path, 0o644)
        with self.assertRaises(dt.KeyError_):
            dt.load_dispatch_key(self.key_path)

    def test_load_rejects_malformed_content(self):
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.write(fd, b"not-a-valid-hex-key")
        os.close(fd)
        with self.assertRaises(dt.KeyError_):
            dt.load_dispatch_key(self.key_path)

    def test_load_missing_raises(self):
        with self.assertRaises(dt.KeyError_):
            dt.load_dispatch_key(self.key_path)


class KeyPathResolutionTests(DispatchTokenTestBase):
    def test_path_for_user_resolves_real_user(self):
        import pwd
        username = pwd.getpwuid(os.getuid()).pw_name
        path = dt.dispatch_key_path_for_user(username)
        # Ends with the relative key path under the user's home.
        self.assertEqual(path.parts[-3:], dt.KEY_RELATIVE_PATH)

    def test_path_for_unknown_user_raises(self):
        with self.assertRaises(dt.KeyError_):
            dt.dispatch_key_path_for_user("no-such-user-xyzzy-42")

    def test_home_override_path(self):
        path = dt.dispatch_key_path(home=self.tmp)
        self.assertEqual(path, self.key_path)


if __name__ == "__main__":
    unittest.main()
