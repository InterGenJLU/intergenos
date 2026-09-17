"""The first-login page always says which machine owner keys the firmware trusts
(R001.3 row 49).

Row 10 made the install offer to retire the machine owner keys earlier installs
left behind, and made this page say when a retirement that was asked for did not
happen. That leaves every other machine silent: a machine nobody asked, or one
installed before the offer existed, can trust several keys — this workstation
trusts four today — and the page said nothing at all about them.

So the page now states the fact on every machine, every time: how many
certificates carrying this project's machine owner name the firmware trusts,
whether this machine's own is among them by fingerprint, which of the others
someone decided to keep and which nobody has decided about, and — when more than
one is trusted — the documented command that retires the rest through the
firmware's own confirmation.

It reads only what a person may read: mokutil's listings, the world-readable
copy of this machine's certificate, and the world-readable record of what was
decided at install time. A count it cannot read is stated as unreadable and
never as zero.
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
_spec = importlib.util.spec_from_file_location("intergen_welcome_trusted", WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)

OWN_DER = b"\x30\x82\x02\x00" + b"this-machines-own-key" * 10
OLD_A = b"\x30\x82\x02\x00" + b"an-earlier-install-a" * 10
OLD_B = b"\x30\x82\x02\x00" + b"an-earlier-install-b" * 10
OWN = hashlib.sha1(OWN_DER).hexdigest()
FP_A = hashlib.sha1(OLD_A).hexdigest()
FP_B = hashlib.sha1(OLD_B).hexdigest()
VENDOR = "2bb010e24d94c632245889baaa9ed0f3d5ef1f68"

OWNER_CN = "InterGenOS Machine Owner Key"


def _colons(fingerprint):
    return ":".join(fingerprint[i:i + 2] for i in range(0, 40, 2))


def _listing(entries):
    """mokutil --list-enrolled output, in the shape mokutil really prints it.

    Taken from a real listing on an installed machine: a [key N] header, an
    Owner line, the SHA1 fingerprint, then the decoded certificate, in which
    BOTH an Issuer line and a Subject line carry a CN, and a "Subject Public Key
    Info:" line follows the subject. A parser that matches "Subject" loosely, or
    that reads the issuer's CN, gets the wrong answer on real output.
    """
    lines = []
    for i, (fingerprint, common_name) in enumerate(entries, start=1):
        lines += [
            f"[key {i}]",
            "Owner: 605dab50-e046-4300-abb6-3dd810dd8b23",
            f"SHA1 Fingerprint: {_colons(fingerprint)}",
            "Certificate:",
            "    Data:",
            "        Version: 3 (0x2)",
            f"        Issuer: CN=some other authority",
            "        Validity",
            f"        Subject: CN={common_name}",
            "        Subject Public Key Info:",
            "            Public Key Algorithm: rsaEncryption",
        ]
    return lines


def _mokutil(entries=(), fails=False):
    def run(*args):
        if args == ("--list-enrolled",):
            return None if fails else _listing(entries)
        return None
    return run


def _own_cert(tmp, der=OWN_DER):
    path = Path(tmp) / "mok.der"
    path.write_bytes(der)
    return str(path)


def _decision_record(tmp, kept=(), retired=()):
    path = Path(tmp) / "mok-prior-keys"
    body = ["# what was decided about earlier machine owner keys",
            "decided=2026-09-17T22:00:00Z"]
    body += [f"kept={f}" for f in kept]
    body += [f"retired={f}" for f in retired]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return str(path)


class TestTheStateItReads(unittest.TestCase):
    def test_one_trusted_key_and_it_is_this_machines(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = welcome._trusted_owner_key_state(
                cert_path=_own_cert(tmp),
                decisions_path=str(Path(tmp) / "absent"),
                mokutil=_mokutil([(OWN, OWNER_CN), (VENDOR, "fedoraca")]))
        self.assertEqual(state["count"], 1)
        self.assertIs(state["own_is_trusted"], True)
        self.assertEqual(state["others"], [])

    def test_vendor_certificates_are_not_counted(self):
        """Only certificates carrying this project's machine owner name count.

        A firmware trusts a vendor's authority too, and counting those would
        tell a person to retire the keys that let their machine boot at all.
        """
        with tempfile.TemporaryDirectory() as tmp:
            state = welcome._trusted_owner_key_state(
                cert_path=_own_cert(tmp),
                decisions_path=str(Path(tmp) / "absent"),
                mokutil=_mokutil([(VENDOR, "fedoraca"),
                                  (OWN, OWNER_CN)]))
        self.assertEqual(state["count"], 1)

    def test_several_trusted_with_this_machines_among_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = welcome._trusted_owner_key_state(
                cert_path=_own_cert(tmp),
                decisions_path=str(Path(tmp) / "absent"),
                mokutil=_mokutil([(FP_A, OWNER_CN), (OWN, OWNER_CN),
                                  (FP_B, OWNER_CN)]))
        self.assertEqual(state["count"], 3)
        self.assertIs(state["own_is_trusted"], True)
        self.assertEqual(sorted(state["others"]), sorted([FP_A, FP_B]))
        self.assertEqual(state["undecided"], sorted([FP_A, FP_B]))
        self.assertEqual(state["kept"], [])

    def test_a_kept_decision_is_read_from_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = welcome._trusted_owner_key_state(
                cert_path=_own_cert(tmp),
                decisions_path=_decision_record(tmp, kept=[FP_A]),
                mokutil=_mokutil([(FP_A, OWNER_CN), (OWN, OWNER_CN),
                                  (FP_B, OWNER_CN)]))
        self.assertEqual(state["kept"], [FP_A])
        self.assertEqual(state["undecided"], [FP_B])

    def test_this_machines_own_key_is_not_among_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = welcome._trusted_owner_key_state(
                cert_path=_own_cert(tmp),
                decisions_path=str(Path(tmp) / "absent"),
                mokutil=_mokutil([(FP_A, OWNER_CN), (FP_B, OWNER_CN)]))
        self.assertEqual(state["count"], 2)
        self.assertIs(state["own_is_trusted"], False)

    def test_an_unreadable_store_is_not_a_count_of_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = welcome._trusted_owner_key_state(
                cert_path=_own_cert(tmp),
                decisions_path=str(Path(tmp) / "absent"),
                mokutil=_mokutil(fails=True))
        self.assertIsNone(state["count"])
        self.assertIsNone(state["own_is_trusted"])

    def test_an_unreadable_own_certificate_leaves_membership_unknown(self):
        """This workstation's real state: four trusted owner certificates, and
        no world-readable copy of the machine's own certificate, because it was
        installed before that copy was staged. Saying "yours is not among them"
        there would be a claim the page cannot support."""
        with tempfile.TemporaryDirectory() as tmp:
            state = welcome._trusted_owner_key_state(
                cert_path=str(Path(tmp) / "no-such-file.der"),
                decisions_path=str(Path(tmp) / "absent"),
                mokutil=_mokutil([(FP_A, OWNER_CN), (FP_B, OWNER_CN)]))
        self.assertEqual(state["count"], 2)
        self.assertIsNone(state["own_is_trusted"])


class TestWhatItSays(unittest.TestCase):
    def _text(self, **kw):
        return welcome._trusted_owner_keys_text(kw)

    def test_the_statement_is_never_silent(self):
        """Every state produces a statement — that is the whole point of the
        change: the page stopped being silent about what the firmware trusts."""
        states = [
            {"count": 1, "own_is_trusted": True, "others": [], "kept": [],
             "undecided": []},
            {"count": 3, "own_is_trusted": True, "others": [FP_A, FP_B],
             "kept": [FP_A], "undecided": [FP_B]},
            {"count": 2, "own_is_trusted": False, "others": [FP_A, FP_B],
             "kept": [], "undecided": [FP_A, FP_B]},
            {"count": 2, "own_is_trusted": None, "others": [FP_A, FP_B],
             "kept": [], "undecided": [FP_A, FP_B]},
            {"count": 0, "own_is_trusted": False, "others": [], "kept": [],
             "undecided": []},
            {"count": None, "own_is_trusted": None, "others": [], "kept": [],
             "undecided": []},
        ]
        # No subTest here, deliberately: a unittest subTest failure is reported
        # by pytest while the test that contains it still prints PASSED, so a
        # reader of the per-test lines would see a green test whose cases
        # failed. Each state is asserted directly, naming itself on failure.
        for state in states:
            text = welcome._trusted_owner_keys_text(state)
            self.assertIsNotNone(text, f"silent in state {state}")
            title, body, action = text
            self.assertTrue(title.strip(), f"no title in state {state}")
            self.assertTrue(body.strip(), f"no body in state {state}")

    def test_one_key_says_so_and_asks_for_nothing(self):
        title, body, action = self._text(count=1, own_is_trusted=True,
                                         others=[], kept=[], undecided=[])
        self.assertIn("one", (title + body).lower())
        self.assertNotIn("mokutil --delete", action)

    def test_several_keys_name_the_documented_removal_path(self):
        title, body, action = self._text(count=3, own_is_trusted=True,
                                         others=[FP_A, FP_B], kept=[],
                                         undecided=[FP_A, FP_B])
        self.assertIn("mokutil --export", action)
        self.assertIn("mokutil --delete", action)

    def test_kept_keys_are_not_written_up_as_a_problem(self):
        title, body, action = self._text(count=3, own_is_trusted=True,
                                         others=[FP_A, FP_B], kept=[FP_A, FP_B],
                                         undecided=[])
        self.assertIn("chose to keep", body)

    def test_undecided_keys_are_named_as_undecided(self):
        title, body, action = self._text(count=3, own_is_trusted=True,
                                         others=[FP_A, FP_B], kept=[],
                                         undecided=[FP_A, FP_B])
        self.assertIn("nobody has decided", body)

    def test_an_unreadable_count_says_unreadable_and_never_zero(self):
        title, body, action = self._text(count=None, own_is_trusted=None,
                                         others=[], kept=[], undecided=[])
        text = (title + " " + body).lower()
        self.assertIn("could not", text)
        for wrong in ("no certificates", "zero", "none of"):
            self.assertNotIn(wrong, text)

    def test_an_unknown_own_key_is_never_reported_as_absent(self):
        title, body, action = self._text(count=2, own_is_trusted=None,
                                         others=[FP_A, FP_B], kept=[],
                                         undecided=[FP_A, FP_B])
        text = (title + " " + body).lower()
        self.assertIn("could not", text)
        self.assertNotIn("is not among", text)


class TestTheBoxItBuilds(unittest.TestCase):
    def test_the_card_is_built_in_every_state(self):
        for count, own in ((1, True), (3, True), (2, False), (2, None),
                           (0, False), (None, None)):
            box = welcome._build_trusted_owner_keys_card(
                {"count": count, "own_is_trusted": own,
                 "others": [FP_A] if (count or 0) > 1 else [],
                 "kept": [], "undecided": [FP_A] if (count or 0) > 1 else []})
            self.assertIsNotNone(box, f"no card for count={count} own={own}")

    def test_a_settled_machine_is_not_painted_as_a_warning(self):
        """One key, this machine's own: a statement, not an advisory. An amber
        box on a machine with nothing to fix teaches people to skim amber."""
        box = welcome._build_trusted_owner_keys_card(
            {"count": 1, "own_is_trusted": True, "others": [], "kept": [],
             "undecided": []})
        classes = box.get_css_classes()
        self.assertIn("intergen-statement", classes)
        self.assertNotIn("intergen-advisory", classes)

    def test_several_trusted_keys_are_an_advisory(self):
        box = welcome._build_trusted_owner_keys_card(
            {"count": 3, "own_is_trusted": True, "others": [FP_A, FP_B],
             "kept": [], "undecided": [FP_A, FP_B]})
        self.assertIn("intergen-advisory", box.get_css_classes())


class TestThePageAlwaysCarriesIt(unittest.TestCase):
    """The wiring, not the wording: a statement the page never appends is a
    statement nobody reads. This builds the real welcome page with every other
    card silent and looks for this one in the widget tree it produced."""

    def _labels(self, widget, out=None):
        out = [] if out is None else out
        if hasattr(widget, "get_label"):
            try:
                text = widget.get_label()
            except TypeError:
                text = None
            if text:
                out.append(text)
        child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
        while child is not None:
            self._labels(child, out)
            child = child.get_next_sibling()
        return out

    def test_the_welcome_page_states_what_the_firmware_trusts(self):
        saved = (welcome._mok_enrolment_state, welcome._mok_retirement_state,
                 welcome._machine_has_a_signing_key,
                 welcome._trusted_owner_key_state)
        try:
            welcome._mok_enrolment_state = lambda *a, **k: None
            welcome._mok_retirement_state = lambda *a, **k: None
            welcome._machine_has_a_signing_key = lambda *a, **k: False
            welcome._trusted_owner_key_state = lambda *a, **k: {
                "count": 4, "own": None, "own_is_trusted": None,
                "others": [FP_A, FP_B], "kept": [], "undecided": [FP_A, FP_B],
                "asked_to_retire": []}
            page = welcome.build_welcome_page()
        finally:
            (welcome._mok_enrolment_state, welcome._mok_retirement_state,
             welcome._machine_has_a_signing_key,
             welcome._trusted_owner_key_state) = saved
        text = " ".join(self._labels(page))
        self.assertIn("firmware trusts", text)
        self.assertIn("four certificates", text)


if __name__ == "__main__":
    unittest.main()
