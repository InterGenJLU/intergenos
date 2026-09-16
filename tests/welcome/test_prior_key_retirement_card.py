"""The Welcomer says when a machine owner key retirement did not happen (R001.3 row 10).

During the install a person can ask for the machine owner keys that earlier
installs left in the firmware to be retired. The firmware does the removing, at
its own prompt, on the next start with Secure Boot on; that prompt waits about
ten seconds and when it is missed the request is dropped and the key store is
left exactly as it was. That is the safe direction and it is also silent, so
this page reads the request files the installer wrote, asks mokutil what is
enrolled and which deletions the firmware still holds, and says which of the two
happened. It reads only; it stays silent when every requested key is gone.
"""

import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WELCOME_PY = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome.py"
_spec = importlib.util.spec_from_file_location("intergen_welcome_retire", WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)

DER_A = b"\x30\x82\x02\x00" + b"prior-key-a" * 20
DER_B = b"\x30\x82\x02\x00" + b"prior-key-b" * 20
FP_A = hashlib.sha1(DER_A).hexdigest()
FP_B = hashlib.sha1(DER_B).hexdigest()
UNRELATED = "2bb010e24d94c632245889baaa9ed0f3d5ef1f68"


def _colons(fingerprint):
    return ":".join(fingerprint[i:i + 2] for i in range(0, 40, 2))


def _request_dir(tmp, files=((FP_A, DER_A),)):
    """The directory the installer writes its retirement requests into."""
    d = Path(tmp) / "enrolled"
    d.mkdir(parents=True, exist_ok=True)
    for name, der in files:
        (d / f"retire-{name}.der").write_bytes(der)
    return str(d)


def _mokutil(enrolled=(), waiting=(), enrolled_fails=False, delete_fails=False):
    """A stand-in for mokutil: answers the two listings the page asks for."""
    def run(*args):
        if args == ("--list-enrolled",):
            return None if enrolled_fails else [
                f"SHA1 Fingerprint: {_colons(f)}" for f in enrolled]
        if args == ("--list-delete",):
            return None if delete_fails else [
                f"SHA1 Fingerprint: {_colons(f)}" for f in waiting]
        raise AssertionError(args)
    return run


class TestRetirementState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_nobody_asked_means_nothing_to_say(self):
        self.assertIsNone(welcome._mok_retirement_state(
            retire_dir=str(Path(self.tmp) / "absent"), mokutil=_mokutil()))
        empty = Path(self.tmp) / "empty"
        empty.mkdir()
        self.assertIsNone(welcome._mok_retirement_state(
            retire_dir=str(empty), mokutil=_mokutil()))

    def test_a_key_the_firmware_still_trusts_is_the_missed_prompt_case(self):
        state = welcome._mok_retirement_state(
            retire_dir=_request_dir(self.tmp),
            mokutil=_mokutil(enrolled=[UNRELATED, FP_A]))
        self.assertEqual(state["requested"], [FP_A])
        self.assertEqual(state["still_trusted"], [FP_A])
        self.assertEqual(state["waiting"], [])
        self.assertEqual(state["gone"], [])

    def test_a_deletion_the_firmware_still_holds_is_not_a_missed_prompt(self):
        state = welcome._mok_retirement_state(
            retire_dir=_request_dir(self.tmp),
            mokutil=_mokutil(enrolled=[FP_A], waiting=[FP_A]))
        self.assertEqual(state["waiting"], [FP_A])
        self.assertEqual(state["still_trusted"], [])

    def test_a_key_the_firmware_no_longer_trusts_is_gone(self):
        state = welcome._mok_retirement_state(
            retire_dir=_request_dir(self.tmp), mokutil=_mokutil(enrolled=[UNRELATED]))
        self.assertEqual(state["gone"], [FP_A])
        self.assertEqual(state["still_trusted"], [])

    def test_a_request_file_whose_name_and_bytes_disagree_is_ignored(self):
        d = Path(self.tmp) / "enrolled"
        d.mkdir(parents=True)
        (d / f"retire-{FP_A}.der").write_bytes(DER_B)
        self.assertIsNone(welcome._mok_retirement_state(
            retire_dir=str(d), mokutil=_mokutil(enrolled=[FP_A])))

    def test_an_empty_request_file_is_ignored(self):
        d = Path(self.tmp) / "enrolled"
        d.mkdir(parents=True)
        (d / f"retire-{FP_A}.der").write_bytes(b"")
        self.assertIsNone(welcome._mok_retirement_state(
            retire_dir=str(d), mokutil=_mokutil(enrolled=[FP_A])))

    def test_a_file_that_is_not_a_request_is_ignored(self):
        d = Path(self.tmp) / "enrolled"
        d.mkdir(parents=True)
        (d / "MOK-0001.der").write_bytes(DER_A)
        self.assertIsNone(welcome._mok_retirement_state(
            retire_dir=str(d), mokutil=_mokutil(enrolled=[FP_A])))

    def test_an_unanswerable_mokutil_says_nothing_rather_than_guessing(self):
        self.assertIsNone(welcome._mok_retirement_state(
            retire_dir=_request_dir(self.tmp),
            mokutil=_mokutil(enrolled=[FP_A], enrolled_fails=True)))

    def test_an_unreadable_delete_listing_does_not_invent_a_missed_prompt(self):
        """`--list-delete` needs privilege on some firmware; unreadable there.

        With no answer the page must not claim the removal is still coming, and
        must not claim it failed either — it reports the key as still trusted,
        which is the state mokutil's enrolled listing actually proves.
        """
        state = welcome._mok_retirement_state(
            retire_dir=_request_dir(self.tmp),
            mokutil=_mokutil(enrolled=[FP_A], delete_fails=True))
        self.assertEqual(state["still_trusted"], [FP_A])
        self.assertEqual(state["waiting"], [])


class TestRetirementCardText(unittest.TestCase):
    def test_silent_when_every_requested_key_is_gone(self):
        self.assertIsNone(welcome._prior_key_card_text(None))
        self.assertIsNone(welcome._prior_key_card_text(
            {"requested": [FP_A], "still_trusted": [], "waiting": [], "gone": [FP_A]}))

    def test_missed_prompt_text_says_what_is_true_and_how_to_ask_again(self):
        title, body, action = welcome._prior_key_card_text(
            {"requested": [FP_A], "still_trusted": [FP_A], "waiting": [], "gone": []})
        self.assertIn("still trusted", title)
        self.assertIn("one machine owner key", body)
        self.assertIn("Nothing was removed", body)
        self.assertIn("10 seconds", body)
        self.assertIn("sudo mokutil --delete", action)
        self.assertIn(os.path.join("/var/lib/intergen/mok/enrolled",
                                   f"retire-{FP_A}.der"), action)

    def test_the_text_never_claims_a_key_was_removed(self):
        for state in (
            {"requested": [FP_A, FP_B], "still_trusted": [FP_A, FP_B],
             "waiting": [], "gone": []},
            {"requested": [FP_A, FP_B], "still_trusted": [],
             "waiting": [FP_A, FP_B], "gone": []},
        ):
            title, body, action = welcome._prior_key_card_text(state)
            words = " ".join((title, body, action)).lower()
            self.assertNotIn("were removed", words)
            self.assertNotIn("has been removed", words)
            self.assertNotIn("retired successfully", words)

    def test_a_partly_done_retirement_reports_the_keys_that_remain(self):
        title, body, action = welcome._prior_key_card_text(
            {"requested": [FP_A, FP_B], "still_trusted": [FP_B],
             "waiting": [], "gone": [FP_A]})
        self.assertIn("two machine owner keys", body)
        self.assertIn("one of them is", body)
        self.assertIn(f"retire-{FP_B}.der", action)
        self.assertNotIn(f"retire-{FP_A}.der", action)

    def test_a_request_the_firmware_still_holds_reads_as_waiting_not_failed(self):
        title, body, action = welcome._prior_key_card_text(
            {"requested": [FP_A], "still_trusted": [], "waiting": [FP_A], "gone": []})
        self.assertIn("waiting for the firmware", title)
        self.assertIn("still holds", body)
        self.assertIn("Restart with Secure Boot on", action)

    def test_counts_read_as_words_for_the_numbers_a_person_will_see(self):
        self.assertEqual(welcome._count_words(1, "machine owner key"),
                         "one machine owner key")
        self.assertEqual(welcome._count_words(7, "machine owner key"),
                         "seven machine owner keys")
        self.assertEqual(welcome._count_words(11, "machine owner key"),
                         "11 machine owner keys")


class TestCardConstruction(unittest.TestCase):
    def test_no_card_when_there_is_nothing_to_say(self):
        self.assertIsNone(welcome._build_prior_key_card(
            {"requested": [FP_A], "still_trusted": [], "waiting": [], "gone": [FP_A]}))

    def test_a_card_is_built_for_the_missed_prompt_case(self):
        card = welcome._build_prior_key_card(
            {"requested": [FP_A], "still_trusted": [FP_A], "waiting": [], "gone": []})
        self.assertIsNotNone(card)


if __name__ == "__main__":
    unittest.main()
