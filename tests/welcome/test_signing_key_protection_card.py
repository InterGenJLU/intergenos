"""The first login says whether this machine's signing key is protected.

The key that signs this machine's boot images and driver modules is encrypted at
rest since 2026-09-17. A machine installed before that carries one with no
passphrase, which is the state where any process running as root can sign a boot
image the firmware trusts — so whether this machine is in that state is worth a
sentence at the first login, beside whether the key is enrolled.

The page runs as the person and cannot read the key: the directory it lives in is
readable only by root, by design. So it reads a small record that the two places
which change the state write — the installer when it makes the key, and the signing
helper when it protects an older one — and it says what that record says, including
saying plainly when there is no record at all.

"No record" is its own answer and is never reported as "protected". A machine
installed before this change has no record and an unprotected key, and the page
says exactly that.
"""

import importlib.util
import unittest
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WELCOME_PY = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome.py"
_spec = importlib.util.spec_from_file_location("intergen_welcome_prot", WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)


def record(tmp_path, text):
    p = Path(tmp_path) / "mok-key-protection"
    p.write_text(text, encoding="utf-8")
    return str(p)


class TestReadingTheRecord(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_a_protected_key_is_read_as_protected(self):
        path = record(self.tmp, "protected=yes\nrecorded=2026-09-17T20:00:00Z\n")
        self.assertIs(welcome._signing_key_protected(path), True)

    def test_an_unprotected_key_is_read_as_unprotected(self):
        path = record(self.tmp, "protected=no\nrecorded=2026-09-17T20:00:00Z\n")
        self.assertIs(welcome._signing_key_protected(path), False)

    def test_no_record_is_neither(self):
        self.assertIsNone(
            welcome._signing_key_protected(str(Path(self.tmp) / "absent")))

    def test_a_record_it_cannot_understand_is_neither(self):
        """Never guess. An unreadable answer is not a reassuring one."""
        path = record(self.tmp, "something else entirely\n")
        self.assertIsNone(welcome._signing_key_protected(path))

    def test_comments_and_blank_lines_are_ignored(self):
        path = record(self.tmp, "# what this file is\n\nprotected=yes\n")
        self.assertIs(welcome._signing_key_protected(path), True)


class TestWhatThePersonIsTold(unittest.TestCase):

    def test_a_protected_key_says_nothing(self):
        """The page advises; it does not congratulate."""
        self.assertIsNone(welcome._signing_key_card_text(True))

    def test_an_unprotected_key_says_what_is_true_and_what_to_do(self):
        text = welcome._signing_key_card_text(False)
        self.assertIsNotNone(text)
        title, body, action = text
        self.assertIn("passphrase", (title + body).lower())
        self.assertIn("root", body.lower())
        self.assertTrue(
            "update" in action.lower() or "kernel" in action.lower(),
            f"the person is not told when they will be asked to set it: {action}")

    def test_no_record_is_stated_as_unknown_rather_than_as_protected(self):
        text = welcome._signing_key_card_text(None)
        self.assertIsNotNone(text, "a machine with no record was told nothing")
        title, body, action = text
        joined = (title + body).lower()
        self.assertTrue("not" in joined or "no record" in joined, joined)


if __name__ == "__main__":
    unittest.main()


class TestAMachineWithNoSigningKeyIsToldNothing(unittest.TestCase):
    """A BIOS install signs nothing and has no key to protect."""

    def test_no_certificate_means_no_card(self):
        self.assertIsNone(
            welcome._build_signing_key_card(protected=None, has_key=False))

    def test_the_certificate_is_what_says_the_machine_has_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            absent = str(Path(tmp) / "mok.der")
            self.assertFalse(welcome._machine_has_a_signing_key(absent))
            present = Path(tmp) / "present.der"
            present.write_bytes(b"\x30\x82certificate")
            self.assertTrue(welcome._machine_has_a_signing_key(str(present)))

    def test_an_empty_certificate_file_is_not_a_key(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.der"
            empty.write_bytes(b"")
            self.assertFalse(welcome._machine_has_a_signing_key(str(empty)))
