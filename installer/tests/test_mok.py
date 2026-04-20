"""Unit tests for installer.backend.mok validation guards.

Covers the pre-condition checks added for HG hardening findings M1 and M2:
- generate_mok_keypair: common_name shell-injection guard
- queue_mok_enrollment: password printable-ASCII guard

Positive paths (subprocess calls) are not exercised here — validation
fires before any shell interaction, so ValueError is testable without
mocking the chroot machinery. Integration coverage for the signed-chain
happy path lives in test_class1_chain_verify.py.
"""

import unittest

from installer.backend import mok


class TestGenerateMokKeypairCommonName(unittest.TestCase):
    """M1: common_name must pass the whitelist before reaching openssl -subj."""

    def test_rejects_single_quote(self):
        # Single quote would break out of the -subj '/CN=...' arg
        with self.assertRaises(ValueError):
            mok.generate_mok_keypair("/tmp/fake", common_name="Machine'; rm -rf /; '")

    def test_rejects_semicolon(self):
        with self.assertRaises(ValueError):
            mok.generate_mok_keypair("/tmp/fake", common_name="CN; id")

    def test_rejects_backslash(self):
        with self.assertRaises(ValueError):
            mok.generate_mok_keypair("/tmp/fake", common_name="Name\\x")

    def test_rejects_command_substitution(self):
        with self.assertRaises(ValueError):
            mok.generate_mok_keypair("/tmp/fake", common_name="$(whoami)")

    def test_rejects_backtick(self):
        with self.assertRaises(ValueError):
            mok.generate_mok_keypair("/tmp/fake", common_name="`id`")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            mok.generate_mok_keypair("/tmp/fake", common_name="")

    def test_rejects_over_64_chars(self):
        with self.assertRaises(ValueError):
            mok.generate_mok_keypair("/tmp/fake", common_name="a" * 65)

    def test_default_common_name_passes_regex(self):
        # Default value must satisfy the whitelist or no install ever works
        self.assertTrue(mok._COMMON_NAME_RE.fullmatch(
            "InterGenOS Machine Owner Key"
        ))

    def test_alnum_with_dots_dashes_underscores_passes(self):
        self.assertTrue(mok._COMMON_NAME_RE.fullmatch("host-01_v2.1 MOK"))


class TestQueueMokEnrollmentPassword(unittest.TestCase):
    """M2: password must be printable ASCII before being piped to mokutil."""

    def test_rejects_newline(self):
        # Embedded newline would split into two stdin password reads
        with self.assertRaises(ValueError):
            mok.queue_mok_enrollment("/tmp/fake", "/fake.der", "pass\nword1")

    def test_rejects_carriage_return(self):
        with self.assertRaises(ValueError):
            mok.queue_mok_enrollment("/tmp/fake", "/fake.der", "pass\rword1")

    def test_rejects_tab(self):
        with self.assertRaises(ValueError):
            mok.queue_mok_enrollment("/tmp/fake", "/fake.der", "pass\tword1")

    def test_rejects_null_byte(self):
        with self.assertRaises(ValueError):
            mok.queue_mok_enrollment("/tmp/fake", "/fake.der", "pass\x00word1")

    def test_rejects_non_ascii(self):
        # Non-ASCII can't be re-typed at MokManager boot prompt
        with self.assertRaises(ValueError):
            mok.queue_mok_enrollment("/tmp/fake", "/fake.der", "passwörd01")

    def test_rejects_del_char(self):
        # 0x7F (DEL) is non-printable even though below 128
        with self.assertRaises(ValueError):
            mok.queue_mok_enrollment("/tmp/fake", "/fake.der", "password\x7f")

    def test_length_check_still_fires(self):
        with self.assertRaises(ValueError):
            mok.queue_mok_enrollment("/tmp/fake", "/fake.der", "short")
        with self.assertRaises(ValueError):
            mok.queue_mok_enrollment("/tmp/fake", "/fake.der", "a" * 257)


if __name__ == "__main__":
    unittest.main()
