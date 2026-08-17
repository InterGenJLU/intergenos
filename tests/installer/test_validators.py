"""installer/backend/_validators.py — validate_password + validate_mok_password.

Audit rows C-050 + C-051 closure: composite weak-password floor.

Pins the installer-side floor (8-char minimum, mirroring MOK's mokutil
ceiling and floor) for both user/root passwords and MOK enrollment.
The PAM-side ships the stronger RHEL 9 baseline (libpwquality minlen=12 +
complexity + faillock) separately; this floor is the installer-side
fail-fast that guarantees install can reach PHASE_MOK without late
failure.
"""

import unittest

from installer.backend._validators import (
    validate_mok_password,
    validate_password,
)


class TestValidatePassword(unittest.TestCase):
    def test_eight_char_minimum_accepts(self):
        self.assertIsNone(validate_password("hunter22"))

    def test_seven_char_rejects(self):
        err = validate_password("hunter2")
        self.assertIsNotNone(err)
        self.assertIn("at least 8", err)

    def test_two_hundred_fifty_six_char_accepts(self):
        self.assertIsNone(validate_password("a" * 256))

    def test_two_hundred_fifty_seven_char_rejects(self):
        err = validate_password("a" * 257)
        self.assertIsNotNone(err)
        self.assertIn("at most 256", err)

    def test_role_label_appears_in_error(self):
        err = validate_password("short", role="root password")
        self.assertIsNotNone(err)
        self.assertIn("root password", err)

    def test_default_role_is_password(self):
        err = validate_password("short")
        self.assertIsNotNone(err)
        self.assertIn("password", err)

    def test_non_string_rejects(self):
        err = validate_password(12345678)
        self.assertIsNotNone(err)
        self.assertIn("must be a string", err)

    def test_none_rejects(self):
        err = validate_password(None)
        self.assertIsNotNone(err)
        self.assertIn("must be a string", err)

    def test_unicode_accepted_if_long_enough(self):
        # validate_password does not constrain ASCII — non-ASCII passwords
        # are valid for user/root accounts (PAM + chpasswd handle them);
        # only MOK enforces ASCII because mokutil is firmware-side.
        self.assertIsNone(validate_password("hünter22"))


class TestValidateMokPassword(unittest.TestCase):
    def test_empty_string_accepts_as_skip_signal(self):
        self.assertIsNone(validate_mok_password(""))

    def test_eight_char_ascii_accepts(self):
        self.assertIsNone(validate_mok_password("hunter22"))

    def test_seven_char_rejects(self):
        err = validate_mok_password("hunter2")
        self.assertIsNotNone(err)
        self.assertIn("8-256", err)

    def test_two_hundred_fifty_seven_char_rejects(self):
        err = validate_mok_password("a" * 257)
        self.assertIsNotNone(err)
        self.assertIn("8-256", err)

    def test_non_ascii_rejects(self):
        err = validate_mok_password("hünter12")
        self.assertIsNotNone(err)
        self.assertIn("printable ASCII", err)

    def test_embedded_newline_rejects(self):
        err = validate_mok_password("foo\nbar1")
        self.assertIsNotNone(err)
        self.assertIn("printable ASCII", err)

    def test_embedded_tab_rejects(self):
        err = validate_mok_password("foo\tbar1")
        self.assertIsNotNone(err)
        self.assertIn("printable ASCII", err)

    def test_embedded_null_rejects(self):
        err = validate_mok_password("foo\x00bar1")
        self.assertIsNotNone(err)
        self.assertIn("printable ASCII", err)

    def test_all_ascii_punctuation_accepts(self):
        # Every printable ASCII char from 32 (space) to 126 (~) must be
        # accepted — mokutil handles them all via stdin.
        s = "".join(chr(c) for c in range(32, 127))
        self.assertIsNone(validate_mok_password(s))

    def test_non_string_rejects(self):
        err = validate_mok_password(12345678)
        self.assertIsNotNone(err)
        self.assertIn("must be a string", err)


if __name__ == "__main__":
    unittest.main()
