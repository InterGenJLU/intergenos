"""The Welcomer's Secure Boot key advisory (R001.3 row 37 (b)).

At the first login the page reads the machine's own certificate from its
public copy, asks mokutil what is enrolled and what is queued, and says in
plain words what to do — only when the key is not enrolled. It never runs
anything privileged and stays silent when there is nothing to say.
"""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WELCOME_PY = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome.py"
_spec = importlib.util.spec_from_file_location("intergen_welcome_sb", WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)

DER = b"\x30\x82\x02\x00" + b"machine-owner-key" * 30
FP = hashlib.sha1(DER).hexdigest()
FP_COLONS = ":".join(FP[i:i + 2] for i in range(0, 40, 2))
OTHER = "2b:b0:10:e2:4d:94:c6:32:24:58:89:ba:aa:9e:d0:f3:d5:ef:1f:68"


def _cert(tmp, data=DER):
    p = Path(tmp) / "mok.der"
    p.write_bytes(data)
    return str(p)


def _mokutil(enrolled=(), queued=(), sb="SecureBoot disabled", fail=False):
    """A stand-in for mokutil: answers the three listings the page asks for."""
    def run(*args):
        if fail:
            return None
        if args == ("--list-enrolled",):
            return [f"SHA1 Fingerprint: {f}" for f in enrolled]
        if args == ("--list-new",):
            return [f"SHA1 Fingerprint: {f}" for f in queued]
        if args == ("--sb-state",):
            return [sb]
        raise AssertionError(args)
    return run


class TestEnrolmentState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_no_staged_certificate_means_nothing_to_say(self):
        self.assertIsNone(welcome._mok_enrolment_state(
            cert_path=str(Path(self.tmp) / "absent.der"), mokutil=_mokutil()))
        self.assertIsNone(welcome._mok_enrolment_state(
            cert_path=_cert(self.tmp, b""), mokutil=_mokutil()))

    def test_enrolled_key_is_recognised_by_fingerprint(self):
        st = welcome._mok_enrolment_state(
            cert_path=_cert(self.tmp), mokutil=_mokutil(enrolled=[OTHER, FP_COLONS]))
        self.assertTrue(st["enrolled"])
        self.assertFalse(st["queued"])
        self.assertEqual(st["fingerprint"], FP)

    def test_not_enrolled_and_queued(self):
        st = welcome._mok_enrolment_state(
            cert_path=_cert(self.tmp),
            mokutil=_mokutil(enrolled=[OTHER], queued=[FP_COLONS], sb="SecureBoot disabled"))
        self.assertEqual((st["enrolled"], st["queued"], st["secure_boot"]),
                         (False, True, False))

    def test_mokutil_unavailable_is_unknown_not_false(self):
        st = welcome._mok_enrolment_state(cert_path=_cert(self.tmp), mokutil=_mokutil(fail=True))
        self.assertEqual((st["enrolled"], st["queued"], st["secure_boot"]), (None, None, None))
        self.assertIsNone(welcome._secure_boot_card_text(st))


class TestCardWording(unittest.TestCase):
    def _state(self, **kw):
        base = {"enrolled": False, "queued": False, "secure_boot": False, "fingerprint": FP}
        base.update(kw)
        return base

    def test_silent_when_enrolled_or_unknown(self):
        self.assertIsNone(welcome._secure_boot_card_text(None))
        self.assertIsNone(welcome._secure_boot_card_text(self._state(enrolled=True)))
        self.assertIsNone(welcome._secure_boot_card_text(self._state(enrolled=None)))

    def test_queued_request_names_the_window_and_the_password(self):
        title, body, action = welcome._secure_boot_card_text(self._state(queued=True))
        self.assertIn("Secure Boot is off", title)
        self.assertIn("10 seconds", body)
        self.assertIn("enrolment password", body)
        self.assertIn("Enroll key from disk", action)
        self.assertIn("EFI > InterGenOS > mok.der", action)
        self.assertIn(FP_COLONS, action)

    def test_nothing_queued_says_the_boot_would_stop(self):
        title, body, action = welcome._secure_boot_card_text(self._state(queued=False, secure_boot=True))
        self.assertIn("Secure Boot is on", title)
        self.assertIn("Verification failed", body)
        self.assertIn("No password is needed", action)


class TestCardWidget(unittest.TestCase):
    """The GTK box builds for a not-enrolled state and is absent otherwise."""

    def setUp(self):
        from gi.repository import Gtk
        if not Gtk.init_check():
            self.skipTest("no display for GTK widget construction")

    def test_builds_with_three_labels_and_the_advisory_class(self):
        from gi.repository import Gtk
        card = welcome._build_secure_boot_card(
            {"enrolled": False, "queued": True, "secure_boot": False, "fingerprint": FP})
        self.assertIsNotNone(card)
        self.assertTrue(card.has_css_class("intergen-advisory"))
        labels = []
        child = card.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label):
                labels.append(child.get_text())
            child = child.get_next_sibling()
        self.assertGreaterEqual(len(labels), 3)
        self.assertIn("Enroll key from disk", " ".join(labels))

    def test_absent_when_enrolled(self):
        self.assertIsNone(welcome._build_secure_boot_card(
            {"enrolled": True, "queued": False, "secure_boot": True, "fingerprint": FP}))


if __name__ == "__main__":
    unittest.main()
