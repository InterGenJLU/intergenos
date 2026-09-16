"""The install writes the same password format the installed system produces (R001.3 row 32).

Measured on an installed R001.2-03 machine before this change: the account the
installer created carries a `$6$` (SHA-512 crypt) hash, while the root account,
whose password was changed afterwards with the ordinary tool, carries `$y$`
(yescrypt). One machine, two formats — and `/etc/login.defs` on that machine
declares no method at all, because the recipe's substitution targeted a line
upstream does not ship, so it matched nothing and changed nothing.

This module covers the installer's half: the hash the install writes is produced
by the system's own password library, in the format that library itself prefers,
so it matches what `passwd` produces on the running machine. The recipe's half —
declaring the method in the shipped configuration — has its own test in
tests/test_row32_login_defs_shipped.py.
"""

import ctypes
import ctypes.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from installer.backend import users  # noqa: E402

PASSWORD = "a-test-password-for-this-suite"


def _libcrypt():
    name = ctypes.util.find_library("crypt") or "libcrypt.so.2"
    return ctypes.CDLL(name, use_errno=True)


def _verify(password, stored):
    """True when the system's own library agrees the hash is that password.

    Hashing the password again with the STORED hash as the salt is how every
    password check works: the stored string carries the method and its
    parameters, so the result must come back byte-identical.
    """
    lib = _libcrypt()
    lib.crypt.restype = ctypes.c_char_p
    lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    again = lib.crypt(password.encode(), stored.encode())
    return again is not None and again.decode() == stored


def _preferred_prefix():
    lib = _libcrypt()
    if not hasattr(lib, "crypt_preferred_method"):
        return None
    lib.crypt_preferred_method.restype = ctypes.c_char_p
    value = lib.crypt_preferred_method()
    return value.decode() if value else None


class TestTheInstallerUsesTheSystemsOwnFormat(unittest.TestCase):
    def test_the_hash_is_in_the_librarys_preferred_format(self):
        preferred = _preferred_prefix()
        if preferred is None:
            self.skipTest("this system's password library states no preferred "
                          "method, so there is nothing to compare against")
        stored = users.hash_password(PASSWORD)
        self.assertTrue(stored.startswith(preferred),
                        f"the install wrote {stored.split('$')[1]!r} where the "
                        f"system's own library prefers {preferred!r}")

    def test_the_hash_verifies_as_the_password_it_was_made_from(self):
        stored = users.hash_password(PASSWORD)
        self.assertTrue(_verify(PASSWORD, stored),
                        "the system's own library must agree the stored hash "
                        "is this password")
        self.assertFalse(_verify(PASSWORD + "x", stored))

    def test_the_hash_is_salted_so_two_runs_differ(self):
        self.assertNotEqual(users.hash_password(PASSWORD),
                            users.hash_password(PASSWORD))

    def test_the_format_written_is_recorded(self):
        events = []
        with patch.object(users.trace, "trace_event",
                          side_effect=lambda name, **kw: events.append((name, kw))):
            stored = users.hash_password(PASSWORD)
        recorded = [kw for name, kw in events if name == "password_hash_format"]
        self.assertEqual(len(recorded), 1,
                         "the format actually written is recorded, so a record "
                         "can never claim a format the install did not produce")
        self.assertEqual(recorded[0]["format"], "$" + stored.split("$")[1] + "$")
        for _name, kw in events:
            for value in kw.values():
                self.assertNotIn(PASSWORD, str(value))
                self.assertNotIn(stored, str(value))


class TestTheFallbackIsLoudNotSilent(unittest.TestCase):
    def test_a_library_that_cannot_hash_falls_back_and_says_so(self):
        events = []
        with patch.object(users, "_libcrypt_hash", return_value=None), \
             patch.object(users.trace, "trace_event",
                          side_effect=lambda name, **kw: events.append((name, kw))):
            stored = users.hash_password(PASSWORD)
        self.assertTrue(stored.startswith("$6$"),
                        "the fallback is SHA-512 crypt, which the install has "
                        "always used and every consumer accepts")
        recorded = [kw for name, kw in events if name == "password_hash_format"]
        self.assertEqual(recorded[0]["format"], "$6$")
        self.assertTrue(recorded[0]["fallback"],
                        "a fallback is recorded AS a fallback — a record that "
                        "looked the same either way would hide it")

    def test_the_fallback_hash_still_verifies(self):
        with patch.object(users, "_libcrypt_hash", return_value=None):
            stored = users.hash_password(PASSWORD)
        self.assertTrue(_verify(PASSWORD, stored))


class TestTheOldNameStillWorks(unittest.TestCase):
    """`_sha512crypt_hash` stays, because it IS the fallback and the existing
    root-password test patches it by name."""

    def test_the_sha512_helper_still_produces_sha512(self):
        self.assertTrue(users._sha512crypt_hash(PASSWORD).startswith("$6$"))


if __name__ == "__main__":
    unittest.main()
