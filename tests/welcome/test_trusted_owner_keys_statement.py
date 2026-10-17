"""The first-login page says nothing about which machine owner keys the firmware
trusts.

The page used to end with a statement, shown on every machine at every login, of
how many certificates carrying this project's machine owner name the firmware
trusts, whether this machine's own is among them by fingerprint, and the mokutil
commands that retire the rest. That statement is removed: the page neither shows
nor mentions the firmware's trusted certificates, enrolled keys, fingerprints or
mokutil.

Two advisories on the same page are not that statement and are unchanged: one
speaks only while this machine's own key is staged and not enrolled, the other
only when a retirement the person chose during the install did not happen. Each
reports a failure of this machine and has its own tests. Here both are silent,
which is the state of almost every machine, so what is left on the page is
exactly what the statement used to add to it.

The firmware is asked through a stand-in mokutil placed first on PATH, so the
page is built in every state the statement could be in: one key named for this
project, several, none but a vendor's, none at all, and a firmware that could
not be asked. The stand-in records every question put to it, and the page must
put none.
"""

import importlib.util
import os
import shlex
import tempfile
import unittest
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WELCOME_PY = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome.py"
_spec = importlib.util.spec_from_file_location("intergen_welcome_trusted", WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)

OWNER_CN = "InterGenOS Machine Owner Key"
FP_A = "8a541b1738e975d378646339be215d6a7892aab0"
FP_B = "17f8adde8d70dee58521b561126051d8879aa23d"
FP_C = "d41d8cd98f00b204e9800998ecf8427e1a2b3c4d"
VENDOR = "2bb010e24d94c632245889baaa9ed0f3d5ef1f68"

# Words the page must not carry, matched without regard to case. The first four
# are the ones the removal names; the rest are the statement's own phrases.
FORBIDDEN = ("mokutil", "fingerprint", "enrolled", "trusted certificate",
             "certificate", "firmware trusts", "machine owner",
             "to ask it yourself")

# What the removal takes out of the module: the statement, everything that fed it,
# and its style.
REMOVED_NAMES = ("_build_trusted_owner_keys_card", "_trusted_owner_key_state",
                 "_trusted_owner_keys_text", "_owner_key_removal_action",
                 "_enrolled_owner_fingerprints", "_subject_common_name",
                 "_statement_box", "_capitalised", "_MOK_OWNER_COMMON_NAME")


def _colons(fingerprint):
    return ":".join(fingerprint[i:i + 2] for i in range(0, 40, 2))


def _listing(entries):
    """mokutil --list-enrolled output, in the shape mokutil really prints it."""
    lines = []
    for i, (fingerprint, common_name) in enumerate(entries, start=1):
        lines += [
            f"[key {i}]",
            "Owner: 605dab50-e046-4300-abb6-3dd810dd8b23",
            f"SHA1 Fingerprint: {_colons(fingerprint)}",
            "Certificate:",
            "    Data:",
            "        Version: 3 (0x2)",
            "        Issuer: CN=some other authority",
            "        Validity",
            f"        Subject: CN={common_name}",
            "        Subject Public Key Info:",
            "            Public Key Algorithm: rsaEncryption",
        ]
    return lines


# The states the statement could be in, as the firmware would answer.
# None is a firmware that could not be asked: mokutil fails and prints nothing.
STATES = {
    "one key named for this project": [(FP_C, OWNER_CN), (VENDOR, "fedoraca")],
    "several": [(FP_A, OWNER_CN), (FP_C, OWNER_CN), (FP_B, OWNER_CN)],
    "none but a vendor's": [(VENDOR, "fedoraca")],
    "none at all": [],
    "a firmware that could not be asked": None,
}


def _labels(widget, out=None):
    """Every label text in the widget tree, however deep."""
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
        _labels(child, out)
        child = child.get_next_sibling()
    return out


def _css_classes(widget, out=None):
    out = set() if out is None else out
    out.update(widget.get_css_classes())
    child = widget.get_first_child()
    while child is not None:
        _css_classes(child, out)
        child = child.get_next_sibling()
    return out


def _forbidden_in(texts):
    """(word, text) for every forbidden word found in the given texts."""
    found = []
    for text in texts:
        low = text.lower()
        for word in FORBIDDEN:
            if word in low:
                found.append((word, text))
    return found


class _StandInFirmware(unittest.TestCase):
    """A stand-in mokutil first on PATH, and the two advisories made silent."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.asked = self.tmp / "asked"
        self.asked.write_text("", encoding="utf-8")
        self._saved_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.tmp / "bin") + os.pathsep + self._saved_path
        self._saved = (welcome._mok_enrolment_state, welcome._mok_retirement_state,
                       welcome._machine_has_a_signing_key)
        welcome._mok_enrolment_state = lambda *a, **k: None
        welcome._mok_retirement_state = lambda *a, **k: None
        welcome._machine_has_a_signing_key = lambda *a, **k: False

    def tearDown(self):
        (welcome._mok_enrolment_state, welcome._mok_retirement_state,
         welcome._machine_has_a_signing_key) = self._saved
        os.environ["PATH"] = self._saved_path
        self._tmp.cleanup()

    def firmware(self, entries):
        """Write the stand-in: it records its arguments, then answers
        --list-enrolled with the listing, or fails with no output when the
        firmware could not be asked."""
        (self.tmp / "bin").mkdir(exist_ok=True)
        listing = self.tmp / "listing"
        listing.write_text("\n".join(_listing(entries or [])) + "\n",
                           encoding="utf-8")
        tool = self.tmp / "bin" / "mokutil"
        answer = ("exit 1" if entries is None
                  else f'[ "$1" = --list-enrolled ] && cat {shlex.quote(str(listing))}\nexit 0')
        tool.write_text("#!/bin/sh\n"
                        f'printf "%s\\n" "$*" >> {shlex.quote(str(self.asked))}\n'
                        + answer + "\n", encoding="utf-8")
        tool.chmod(0o755)

    def questions(self):
        return self.asked.read_text(encoding="utf-8").splitlines()


class TestTheFirstPageSaysNothingAboutTrustedKeys(_StandInFirmware):
    """Each state the statement could be in, one test each: a subTest failure is
    reported by pytest while its parent test still prints PASSED."""

    def _assert_silent_in(self, state):
        self.firmware(STATES[state])
        page = welcome.build_welcome_page()
        texts = _labels(page)
        self.assertTrue(any("Welcome to InterGenOS" in t for t in texts),
                        f"the page itself was not built in state {state!r}")
        self.assertEqual(_forbidden_in(texts), [],
                         f"the first page names the trusted keys in state {state!r}")
        self.assertEqual(self.questions(), [],
                         f"the first page asked the firmware in state {state!r}")
        classes = _css_classes(page)
        self.assertNotIn("intergen-statement", classes,
                         f"a statement box is on the page in state {state!r}")
        self.assertNotIn("intergen-advisory", classes,
                         f"an advisory box is on the page in state {state!r}")

    def test_one_key_named_for_this_project(self):
        self._assert_silent_in("one key named for this project")

    def test_several_keys_named_for_this_project(self):
        self._assert_silent_in("several")

    def test_only_a_vendors_key(self):
        self._assert_silent_in("none but a vendor's")

    def test_no_key_at_all(self):
        self._assert_silent_in("none at all")

    def test_a_firmware_that_could_not_be_asked(self):
        self._assert_silent_in("a firmware that could not be asked")


class TestTheStatementIsGoneFromTheModule(unittest.TestCase):
    def test_the_statement_and_what_fed_it_are_gone(self):
        present = [name for name in REMOVED_NAMES if hasattr(welcome, name)]
        self.assertEqual(present, [])

    def test_the_statement_style_is_gone(self):
        self.assertNotIn("intergen-statement", welcome.CUSTOM_CSS)


class TestTheChecksSeeWhatTheyLookFor(_StandInFirmware):
    """The planted-text control: each check above, shown finding the thing it
    looks for, so that its empty answer on the real page means something."""

    def test_a_planted_statement_is_found_where_the_cards_are_appended(self):
        self.firmware(STATES["several"])
        page = welcome.build_welcome_page()
        title = [w for w in self._walk(page)
                 if isinstance(w, Gtk.Label)
                 and w.get_label() == "Welcome to InterGenOS"]
        self.assertEqual(len(title), 1)
        planted = ("This machine's firmware trusts four certificates named "
                   "for this project",
                   "To ask it yourself, in a terminal: mokutil --list-enrolled")
        box = title[0].get_parent()
        for text in planted:
            box.append(Gtk.Label(label=text))
        box.add_css_class("intergen-statement")
        # Only the planted lines are counted here, so this control reads the
        # same on a page that still carries the statement and on one that does
        # not; whether the page itself is clean is what the tests above decide.
        found = [(word, text) for word, text in _forbidden_in(_labels(page))
                 if text in planted]
        self.assertEqual(sorted({text for _, text in found}), sorted(planted))
        self.assertEqual(sorted(word for word, _ in found),
                         sorted(["firmware trusts", "certificate",
                                 "mokutil", "enrolled", "to ask it yourself"]))
        self.assertIn("intergen-statement", _css_classes(page))

    def test_a_planted_question_to_the_firmware_is_recorded(self):
        self.firmware(STATES["several"])
        lines = welcome._mokutil_lines("--list-enrolled")
        self.assertEqual(self.questions(), ["--list-enrolled"])
        self.assertIn(f"SHA1 Fingerprint: {_colons(FP_A)}", lines)

    def test_the_stand_in_fails_as_a_firmware_that_could_not_be_asked(self):
        self.firmware(STATES["a firmware that could not be asked"])
        self.assertIsNone(welcome._mokutil_lines("--list-enrolled"))
        self.assertEqual(self.questions(), ["--list-enrolled"])

    def _walk(self, widget):
        yield widget
        child = widget.get_first_child()
        while child is not None:
            yield from self._walk(child)
            child = child.get_next_sibling()


if __name__ == "__main__":
    unittest.main()
