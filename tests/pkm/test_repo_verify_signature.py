#!/usr/bin/env python3
"""Q10 pytest unit test for pkm.repo.RepoManager._verify_signature.

Authored 2026-05-19 after operator committed InterGenOS to Linux-only
dev/test target. The prior Windows gpg-agent IPC constraint at
Python-tempfile paths was the original blocker on cross-platform
pytest; Linux has no such constraint so the test can run natively.

Coverage:
  - GREEN path: ephemeral key signs payload + key FP is in
    PINNED_RELEASE_FINGERPRINTS -> _verify_signature returns True
  - RED path 1: ephemeral key signs payload + key FP NOT in pinned set
    -> _verify_signature returns False (L-025 keyring-swap defense)
  - RED path 2: data file corrupted post-signature -> gpg returncode
    non-zero -> _verify_signature returns False
  - RED path 3: signature file corrupted -> gpg returncode non-zero ->
    _verify_signature returns False
  - RED path 4: empty pin set -> no FP can match even with valid sig
    -> _verify_signature returns False (fail-closed per docstring)

Pattern: generate ephemeral GPG keypair in a tempdir (via --homedir
to avoid mutating process-wide GNUPGHOME env), export pubkey + import
to a separate target keyring at GPG_KEYRING (patched), sign a test
payload, then call _verify_signature with various PINNED_RELEASE_
FINGERPRINTS configurations. The same three end-to-end links that
`scripts/validate-keyring-rotation.sh` exercises in bash are
exercised here at the Python wrapper layer.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pkm.repo
from pkm.repo import RepoManager


def _gpg(*args, gnupghome=None, input_text=None, check=True):
    """Wrapper around subprocess.run for gpg invocations.

    `gnupghome` (Path) is passed via --homedir so we don't mutate the
    process-wide GNUPGHOME env var (which would leak across tests).
    """
    cmd = ["gpg"]
    if gnupghome is not None:
        cmd.extend(["--homedir", str(gnupghome)])
    cmd.extend(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=input_text,
        check=check,
    )


def _generate_ephemeral_key(gnupghome):
    """Generate an ephemeral RSA-2048 key in `gnupghome`.

    Returns the uppercase 40-hex-char fingerprint.
    """
    batch = gnupghome / "batch-gen-key"
    batch.write_text(
        "Key-Type: RSA\n"
        "Key-Length: 2048\n"
        "Name-Real: Test Key\n"
        "Name-Email: test@intergenos.test\n"
        "Expire-Date: 0\n"
        "%no-protection\n"
        "%commit\n"
    )
    _gpg("--batch", "--no-tty", "--gen-key", str(batch), gnupghome=gnupghome)
    result = _gpg("--list-keys", "--with-colons", gnupghome=gnupghome)
    for line in result.stdout.splitlines():
        if line.startswith("fpr:"):
            return line.split(":")[9].upper()
    raise RuntimeError("No fingerprint found after key generation")


def _import_pubkey_to_keyring(gnupghome, target_keyring):
    """Export pubkey from `gnupghome` and import into `target_keyring`."""
    asc = _gpg("--armor", "--export", gnupghome=gnupghome).stdout
    subprocess.run(
        ["gpg", "--no-default-keyring", "--keyring", str(target_keyring),
         "--batch", "--import"],
        input=asc, text=True, capture_output=True, check=True,
    )


def _sign_data(gnupghome, fingerprint, data_path, sig_path):
    """Detached-sign `data_path` with the ephemeral key; output to `sig_path`."""
    _gpg(
        "--batch", "--yes", "--detach-sign",
        "--local-user", fingerprint,
        "--output", str(sig_path), str(data_path),
        gnupghome=gnupghome,
    )


@unittest.skipUnless(shutil.which("gpg"), "gpg(1) not available in PATH")
class VerifySignatureTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.gnupghome = self.tmp / "gnupg"
        self.gnupghome.mkdir(mode=0o700)
        self.target_keyring = self.tmp / "trusted.gpg"
        self.data_path = self.tmp / "data.txt"
        self.sig_path = self.tmp / "data.txt.sig"
        self.data_path.write_text("test payload\n")

        # Generate ephemeral keypair + sign data + import pubkey to the
        # target keyring (which is what _verify_signature checks against
        # after we patch pkm.repo.GPG_KEYRING).
        self.fingerprint = _generate_ephemeral_key(self.gnupghome)
        _import_pubkey_to_keyring(self.gnupghome, self.target_keyring)
        _sign_data(self.gnupghome, self.fingerprint, self.data_path, self.sig_path)

        self._patch_keyring = patch.object(
            pkm.repo, "GPG_KEYRING", self.target_keyring,
        )
        self._patch_keyring.start()

    def tearDown(self):
        self._patch_keyring.stop()
        # Kill any lingering gpg-agent for this tempdir (gpg 2.x spawns
        # one per GNUPGHOME on first use; leaving it running can prevent
        # the tempdir from cleaning up).
        try:
            _gpg("--batch", "--no-tty", "--quiet", "--kill-agent",
                 "gpg-agent", gnupghome=self.gnupghome, check=False)
        except Exception:
            pass
        self._tmpdir.cleanup()

    def _make_repo_manager(self):
        # Bypass __init__ to avoid the cache-dir creation side effects
        # (REPO_DB_CACHE.mkdir + chmod). _verify_signature does not
        # touch self.* so this is safe.
        return RepoManager.__new__(RepoManager)

    def test_green_path_pinned_fp_returns_true(self):
        with patch.object(
            pkm.repo, "PINNED_RELEASE_FINGERPRINTS",
            frozenset({self.fingerprint}),
        ):
            rm = self._make_repo_manager()
            self.assertTrue(rm._verify_signature(self.data_path, self.sig_path))

    def test_red_unknown_fingerprint_returns_false(self):
        # Signature verifies against the keyring, but the FP is not in
        # the pinned set -- L-025 keyring-swap defense.
        unknown_fp = "0" * 40
        with patch.object(
            pkm.repo, "PINNED_RELEASE_FINGERPRINTS",
            frozenset({unknown_fp}),
        ):
            rm = self._make_repo_manager()
            self.assertFalse(rm._verify_signature(self.data_path, self.sig_path))

    def test_red_corrupted_data_returns_false(self):
        # Modify data post-sign; gpg --verify fails -> False.
        self.data_path.write_text("modified payload\n")
        with patch.object(
            pkm.repo, "PINNED_RELEASE_FINGERPRINTS",
            frozenset({self.fingerprint}),
        ):
            rm = self._make_repo_manager()
            self.assertFalse(rm._verify_signature(self.data_path, self.sig_path))

    def test_red_corrupted_signature_returns_false(self):
        self.sig_path.write_bytes(b"not a valid signature")
        with patch.object(
            pkm.repo, "PINNED_RELEASE_FINGERPRINTS",
            frozenset({self.fingerprint}),
        ):
            rm = self._make_repo_manager()
            self.assertFalse(rm._verify_signature(self.data_path, self.sig_path))

    def test_red_empty_pin_set_returns_false(self):
        # Valid sig + valid keyring + empty pin set -> fail-closed.
        with patch.object(
            pkm.repo, "PINNED_RELEASE_FINGERPRINTS",
            frozenset(),
        ):
            rm = self._make_repo_manager()
            self.assertFalse(rm._verify_signature(self.data_path, self.sig_path))


if __name__ == "__main__":
    unittest.main()
