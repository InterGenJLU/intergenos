# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The machine owner signing key is protected at rest by the owner's passphrase.

Measured on a real install before this test existed: /var/lib/intergen/mok/mok.key
was a plain RSA private key, so any process running as root could sign a boot image
the firmware trusts, with no person at the gate. Secure Boot stopped an attacker
without root and nobody with root.

These tests assert the END STATE rather than one implementation of it: after the
installer has generated the keypair, reading the private key with an EMPTY
passphrase must fail and reading it with the owner's passphrase must succeed. That
pair is the whole point — the second assertion alone would pass against a plain key,
because a plain key reads back under any passphrase argument at all.

They run the real openssl on real files in a temporary directory rather than mocking
it. A mock cannot tell an encrypted key from a plain one.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from installer.backend import mok


PASSPHRASE = "owner-passphrase-1"


def read_key_with(key_path, passphrase):
    """(returncode, stderr) from openssl reading the private key with `passphrase`.

    The passphrase travels in this subprocess's environment, never on the command
    line, for the same reason the installer does it that way: an argument is visible
    in the process table to every user on the machine.
    """
    env = dict(os.environ)
    env["IGOS_TEST_PASS"] = passphrase
    proc = subprocess.run(
        ["openssl", "rsa", "-in", str(key_path), "-check", "-noout",
         "-passin", "env:IGOS_TEST_PASS"],
        env=env, capture_output=True, text=True)
    return proc.returncode, proc.stderr


class TestGeneratedKeyIsEncryptedAtRest(unittest.TestCase):
    """The generated private key cannot be read without the owner's passphrase."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="igos-mok-test-")
        self.target = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_key_refuses_to_read_with_an_empty_passphrase(self):
        # The negative control. This is the exact command that proved the shipped
        # key was plain: against a plain key it answers "RSA key ok".
        mok.generate_mok_keypair(self.target, passphrase=PASSPHRASE)
        key = self.target / mok.MOK_DIR.lstrip("/") / "mok.key"
        rc, _stderr = read_key_with(key, "")
        self.assertNotEqual(rc, 0,
                            "the private key read back with an EMPTY passphrase, "
                            "which means it is stored unencrypted")

    def test_key_reads_with_the_owner_passphrase(self):
        mok.generate_mok_keypair(self.target, passphrase=PASSPHRASE)
        key = self.target / mok.MOK_DIR.lstrip("/") / "mok.key"
        rc, stderr = read_key_with(key, PASSPHRASE)
        self.assertEqual(rc, 0, f"the owner's own passphrase did not open the key: {stderr}")

    def test_pem_header_says_encrypted(self):
        mok.generate_mok_keypair(self.target, passphrase=PASSPHRASE)
        key = self.target / mok.MOK_DIR.lstrip("/") / "mok.key"
        first = key.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("ENCRYPTED", first,
                      f"first PEM line is {first!r}; an unencrypted key says "
                      f"'-----BEGIN PRIVATE KEY-----'")

    def test_certificate_still_usable(self):
        """Encrypting the private half must not disturb the public half."""
        paths = mok.generate_mok_keypair(self.target, passphrase=PASSPHRASE)
        cert = self.target / paths["cert_path"].lstrip("/")
        der = self.target / paths["der_path"].lstrip("/")
        self.assertTrue(cert.is_file() and cert.stat().st_size > 0)
        self.assertTrue(der.is_file() and der.stat().st_size > 0)
        proc = subprocess.run(
            ["openssl", "x509", "-in", str(cert), "-noout", "-subject"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestGenerationRefusesWithoutAPassphrase(unittest.TestCase):
    """No caller can obtain a plain key by omitting the passphrase."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="igos-mok-test-")
        self.target = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_missing_passphrase_raises(self):
        with self.assertRaises(ValueError):
            mok.generate_mok_keypair(self.target)

    def test_empty_passphrase_raises(self):
        with self.assertRaises(ValueError):
            mok.generate_mok_keypair(self.target, passphrase="")

    def test_no_key_file_is_left_behind_when_it_refuses(self):
        """A refusal must not leave a half-made key on the target."""
        with self.assertRaises(ValueError):
            mok.generate_mok_keypair(self.target, passphrase="")
        key = self.target / mok.MOK_DIR.lstrip("/") / "mok.key"
        self.assertFalse(key.exists(), "a refused generation left a key file behind")

    def test_control_character_in_passphrase_raises(self):
        """A newline would split the line the signing tools read from stdin."""
        with self.assertRaises(ValueError):
            mok.generate_mok_keypair(self.target, passphrase="two\nlines")


class TestPassphraseNeverReachesACommandLine(unittest.TestCase):
    """The passphrase is never an argument, because arguments are world-readable."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="igos-mok-test-")
        self.target = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_openssl_argv_does_not_carry_the_passphrase(self):
        seen = []
        real_run = subprocess.run

        def recording_run(cmd, *args, **kwargs):
            seen.append(cmd)
            return real_run(cmd, *args, **kwargs)

        import installer.backend.mok as mok_mod
        original = mok_mod.subprocess.run
        mok_mod.subprocess.run = recording_run
        try:
            mok.generate_mok_keypair(self.target, passphrase=PASSPHRASE)
        finally:
            mok_mod.subprocess.run = original

        self.assertTrue(seen, "no subprocess was recorded")
        for cmd in seen:
            flat = " ".join(str(part) for part in cmd)
            self.assertNotIn(PASSPHRASE, flat,
                             f"the passphrase appeared in a command line: {flat}")
            # `pass:` with a value after it puts that value in the process
            # table. `pass:` with NOTHING after it is the empty-passphrase
            # negative control, which carries no secret and is the check that
            # proves the key is encrypted at all — so the rule is about a
            # non-empty value, not about the word.
            for part in cmd:
                part = str(part)
                self.assertFalse(
                    part.startswith("pass:") and part != "pass:",
                    f"a passphrase value was passed as an argument: {part}")

    def test_passphrase_is_not_written_to_any_file_under_the_target(self):
        mok.generate_mok_keypair(self.target, passphrase=PASSPHRASE)
        needle = PASSPHRASE.encode()
        for path in self.target.rglob("*"):
            if not path.is_file():
                continue
            self.assertNotIn(needle, path.read_bytes(),
                             f"the passphrase was written into {path}")


class TestReadBackIsAnInstallCheck(unittest.TestCase):
    """A key that reads back without a passphrase fails the install, not a warning."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="igos-mok-test-")
        self.target = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_verify_accepts_a_properly_encrypted_key(self):
        paths = mok.generate_mok_keypair(self.target, passphrase=PASSPHRASE)
        # Must not raise.
        mok.verify_key_is_encrypted(self.target, paths["key_path"], PASSPHRASE)

    def test_verify_rejects_a_plain_key(self):
        """The check must FAIL on the state the machines actually shipped in."""
        mok_dir = self.target / mok.MOK_DIR.lstrip("/")
        mok_dir.mkdir(parents=True, exist_ok=True)
        key = mok_dir / "mok.key"
        subprocess.run(
            ["openssl", "genrsa", "-out", str(key), "2048"],
            capture_output=True, text=True, check=True)
        with self.assertRaises(RuntimeError):
            mok.verify_key_is_encrypted(
                self.target, f"{mok.MOK_DIR}/mok.key", PASSPHRASE)

    def test_verify_rejects_a_key_the_passphrase_does_not_open(self):
        paths = mok.generate_mok_keypair(self.target, passphrase=PASSPHRASE)
        with self.assertRaises(RuntimeError):
            mok.verify_key_is_encrypted(
                self.target, paths["key_path"], "not-the-right-one")


class TestPassphraseStaysOutOfTheInstallTrace(unittest.TestCase):
    """The variable's NAME is what keeps its value out of the durable trace."""

    def test_the_variable_name_is_covered_by_the_trace_redactor(self):
        from installer.backend import trace
        name = mok._PASS_ENV
        self.assertTrue(
            any(marker in name.upper() for marker in trace.REDACT_ENV_SUBSTRINGS),
            f"{name} matches none of {trace.REDACT_ENV_SUBSTRINGS}, so the "
            f"install trace would record the passphrase in full")

    def test_the_redactor_actually_scrubs_this_variable(self):
        """Read the instrument, not the rule it is supposed to implement.

        The positive control comes first: a variable the redactor is NOT
        supposed to scrub must come back intact, or a redactor that blanked
        everything would pass this test while telling us nothing.
        """
        import sys
        repo = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(repo / "scripts" / "lib"))
        try:
            import igos_trace
        finally:
            sys.path.pop(0)

        untouched = igos_trace._redact_env({"LANG": "en_US.UTF-8"})
        self.assertEqual(untouched, {"LANG": "en_US.UTF-8"},
                         "the redactor scrubs everything, so scrubbing the "
                         "passphrase proves nothing")

        scrubbed = igos_trace._redact_env({mok._PASS_ENV: PASSPHRASE})
        self.assertNotIn(PASSPHRASE, str(scrubbed))


class TestTheEnvironmentWindowCloses(unittest.TestCase):
    """The passphrase does not linger in the installer's own environment."""

    def test_variable_is_removed_after_the_block(self):
        self.assertNotIn(mok._PASS_ENV, os.environ)
        with mok.passphrase_in_environment(PASSPHRASE):
            self.assertEqual(os.environ[mok._PASS_ENV], PASSPHRASE)
        self.assertNotIn(mok._PASS_ENV, os.environ)

    def test_variable_is_removed_when_the_block_raises(self):
        with self.assertRaises(RuntimeError):
            with mok.passphrase_in_environment(PASSPHRASE):
                raise RuntimeError("the install failed mid-phase")
        self.assertNotIn(mok._PASS_ENV, os.environ)

    def test_no_passphrase_is_a_quiet_no_op(self):
        with mok.passphrase_in_environment(None):
            self.assertNotIn(mok._PASS_ENV, os.environ)


class TestTheProtectionStateIsRecordedForThePerson(unittest.TestCase):
    """The first-login page runs as the person and cannot read the key."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="igos-mok-test-")
        self.target = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def record_path(self):
        return self.target / mok.MOK_PROTECTION_RECORD.lstrip("/")

    def test_generating_the_key_records_that_it_is_protected(self):
        mok.generate_mok_keypair(self.target, passphrase=PASSPHRASE)
        text = self.record_path().read_text(encoding="utf-8")
        self.assertIn("protected=yes", text)

    def test_the_record_is_readable_by_the_person(self):
        mok.generate_mok_keypair(self.target, passphrase=PASSPHRASE)
        mode = self.record_path().stat().st_mode & 0o777
        self.assertEqual(mode, 0o644,
                         "the page that reads this runs as the person, not root")

    def test_the_record_carries_no_secret(self):
        mok.generate_mok_keypair(self.target, passphrase=PASSPHRASE)
        self.assertNotIn(PASSPHRASE, self.record_path().read_text(encoding="utf-8"))

    def test_the_record_says_what_it_is(self):
        """A person who cats this file should understand it without a manual."""
        mok.generate_mok_keypair(self.target, passphrase=PASSPHRASE)
        text = self.record_path().read_text(encoding="utf-8").lower()
        self.assertIn("passphrase", text)
        self.assertIn("boot", text)

    def test_nothing_is_recorded_when_generation_refuses(self):
        with self.assertRaises(ValueError):
            mok.generate_mok_keypair(self.target, passphrase="")
        self.assertFalse(self.record_path().exists())


if __name__ == "__main__":
    unittest.main()
