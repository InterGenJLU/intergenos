"""The retirement card works on a machine where it cannot read the request files.

Row 10's card reads /var/lib/intergen/mok/enrolled to learn which keys the
install asked the firmware to remove. That directory's parent is mode 0700
root:root on an installed machine and this page runs as the person, so the
listing raises PermissionError, the state function returns None, and the card is
silent — on every machine, including one whose firmware trusts four machine
owner certificates. Measured on a real install 2026-09-17; the capture is
30-finding-row10-card-is-blind.log in this cut's evidence.

The fix reads what the page may read: the world-readable record the install now
writes. When the request directory cannot be listed, the fingerprints the record
marks as retired are the request list, and the card says the same thing it would
have said. When the directory CAN be listed, nothing changes — the files are
still checked against their own names, byte for byte.
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
_spec = importlib.util.spec_from_file_location("intergen_welcome_retire_fallback",
                                               WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)

DER_A = b"\x30\x82\x02\x00" + b"prior-key-a" * 20
FP_A = hashlib.sha1(DER_A).hexdigest()


def _colons(f):
    return ":".join(f[i:i + 2] for i in range(0, 40, 2))


def _mokutil(enrolled=(), waiting=()):
    def run(*args):
        if args == ("--list-enrolled",):
            return [f"SHA1 Fingerprint: {_colons(f)}" for f in enrolled]
        if args == ("--list-delete",):
            return [f"SHA1 Fingerprint: {_colons(f)}" for f in waiting]
        return None
    return run


def _record(tmp, retired=(), kept=()):
    path = Path(tmp) / "mok-prior-keys"
    lines = ["# record", "decided=2026-09-17T22:00:00Z"]
    lines += [f"retired={f}" for f in retired] + [f"kept={f}" for f in kept]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


class TestTheUnreadableDirectory(unittest.TestCase):
    def test_an_unreadable_directory_falls_back_to_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = welcome._mok_retirement_state(
                retire_dir=str(Path(tmp) / "no-such-dir"),
                decisions_path=_record(tmp, retired=[FP_A]),
                mokutil=_mokutil(enrolled=[FP_A]))
        self.assertIsNotNone(state, "silent on the machine it was written for")
        self.assertEqual(state["requested"], [FP_A])
        self.assertEqual(state["still_trusted"], [FP_A])
        self.assertTrue(state["from_record"])

    def test_an_unreadable_directory_and_no_record_stays_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = welcome._mok_retirement_state(
                retire_dir=str(Path(tmp) / "no-such-dir"),
                decisions_path=str(Path(tmp) / "no-such-record"),
                mokutil=_mokutil(enrolled=[FP_A]))
        self.assertIsNone(state)

    def test_a_readable_directory_still_checks_the_bytes(self):
        """Unchanged behaviour where the directory can be read: a request file
        whose name and content disagree is ignored, not trusted."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "enrolled"
            d.mkdir()
            (d / f"retire-{FP_A}.der").write_bytes(DER_A)
            (d / f"retire-{'f' * 40}.der").write_bytes(b"not that certificate")
            state = welcome._mok_retirement_state(
                retire_dir=str(d),
                decisions_path=_record(tmp, retired=[FP_A, "f" * 40]),
                mokutil=_mokutil(enrolled=[FP_A]))
        self.assertEqual(state["requested"], [FP_A])
        self.assertFalse(state["from_record"])

    def test_the_card_names_the_documented_path_when_it_read_the_record(self):
        """It never saw the request files, so it does not print their names as
        though it had: it gives the documented export-and-delete path instead."""
        text = welcome._prior_key_card_text({
            "requested": [FP_A], "still_trusted": [FP_A], "waiting": [],
            "gone": [], "from_record": True})
        self.assertIsNotNone(text)
        title, body, action = text
        self.assertIn("mokutil --export", action)
        self.assertNotIn("/var/lib/intergen/mok/enrolled/retire-", action)

    def test_the_card_still_names_the_files_when_it_saw_them(self):
        text = welcome._prior_key_card_text({
            "requested": [FP_A], "still_trusted": [FP_A], "waiting": [],
            "gone": [], "from_record": False})
        title, body, action = text
        self.assertIn(f"/var/lib/intergen/mok/enrolled/retire-{FP_A}.der", action)


if __name__ == "__main__":
    unittest.main()
