# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""R001.3 gating row 32 — an encrypted install can offer a second way in.

An encrypted install ends with exactly one way to unlock the disk: the
passphrase the person chose. Forget it, or mistype it into a keyboard
layout that is not the one they installed with, and the disk is gone.
The correction the row asks for is an OFFER at install time — the person
chooses — to add a second key slot holding a recovery key the machine
generates and shows them once.

The recovery key is only worth something if the slot actually opens the
volume, so adding it is not finished until cryptsetup itself has been
asked whether the new key works. A slot that was written but cannot
unlock is the exact failure this feature exists to prevent.
"""

import unittest
from unittest.mock import patch

from installer.backend import disks


class TestTheRecoveryKeyItself(unittest.TestCase):

    def test_it_is_grouped_for_a_person_to_write_down(self):
        key = disks.generate_recovery_key()
        groups = key.split("-")
        self.assertEqual(len(groups), disks.RECOVERY_KEY_GROUPS)
        for group in groups:
            self.assertEqual(len(group), disks.RECOVERY_KEY_GROUP_LEN)

    def test_it_avoids_the_characters_people_transcribe_wrongly(self):
        alphabet = set(disks.RECOVERY_KEY_ALPHABET)
        for ambiguous in "OIL01":
            self.assertNotIn(ambiguous, alphabet)
        key = disks.generate_recovery_key()
        self.assertTrue(set(key.replace("-", "")) <= alphabet)

    def test_two_keys_are_not_the_same_key(self):
        keys = {disks.generate_recovery_key() for _ in range(25)}
        self.assertEqual(len(keys), 25)

    def test_it_carries_enough_entropy_to_be_worth_generating(self):
        import math
        bits = (disks.RECOVERY_KEY_GROUPS * disks.RECOVERY_KEY_GROUP_LEN
                * math.log2(len(disks.RECOVERY_KEY_ALPHABET)))
        self.assertGreaterEqual(bits, 128)


class TestAddingTheSlot(unittest.TestCase):

    def _add(self, add_ok=True, verify_rc=0):
        added = {}

        def fake_add(device, existing, new):
            if not add_ok:
                raise RuntimeError("cryptsetup luksAddKey failed (exit 1): no slot free")
            added["device"] = device
            added["existing"] = existing
            added["new"] = new

        class Result:
            returncode = verify_rc
            stderr = b"No key available with this passphrase."
            stdout = b""

        commands = []

        def fake_run(cmd, **kwargs):
            commands.append((cmd, kwargs))
            return Result()

        with patch.object(disks, "_luks_add_key_with_existing", side_effect=fake_add), \
             patch.object(disks.trace, "traced_run", side_effect=fake_run):
            result = disks.add_recovery_key_slot("/dev/sda2", "the chosen passphrase",
                                                 "AAAAA-BBBBB")
        return result, added, commands

    def test_the_slot_is_added_with_the_existing_passphrase(self):
        result, added, _ = self._add()
        self.assertEqual(added["device"], "/dev/sda2")
        self.assertEqual(added["existing"], b"the chosen passphrase")
        self.assertEqual(added["new"], b"AAAAA-BBBBB")
        self.assertTrue(result["verified"])

    def test_the_new_key_is_tried_against_the_volume_before_it_is_believed(self):
        _, _, commands = self._add()
        self.assertEqual(len(commands), 1, "the added slot must be tested")
        cmd, kwargs = commands[0]
        self.assertIn("--test-passphrase", cmd)
        self.assertIn("/dev/sda2", cmd)
        self.assertEqual(kwargs.get("input"), b"AAAAA-BBBBB")

    def test_the_recovery_key_is_never_passed_as_a_command_argument(self):
        _, _, commands = self._add()
        cmd, _ = commands[0]
        for argument in cmd:
            self.assertNotIn("AAAAA-BBBBB", argument)

    def test_a_slot_that_does_not_open_the_volume_is_an_error(self):
        with self.assertRaises(RuntimeError) as caught:
            self._add(verify_rc=1)
        self.assertIn("could not unlock", str(caught.exception).lower())

    def test_a_refused_add_is_not_swallowed(self):
        with self.assertRaises(RuntimeError):
            self._add(add_ok=False)


class TestTheInstallOffersIt(unittest.TestCase):

    def test_the_partitioner_takes_the_choice_and_returns_the_key_once(self):
        import inspect
        signature = inspect.signature(disks.partition_disk)
        self.assertIn("recovery_key_enabled", signature.parameters)
        self.assertIs(signature.parameters["recovery_key_enabled"].default, False)

    def test_an_unencrypted_install_cannot_ask_for_one(self):
        with self.assertRaises(ValueError) as caught:
            disks.partition_disk("/dev/sda", efi=True, luks_enabled=False,
                                 recovery_key_enabled=True)
        self.assertIn("recovery_key_enabled", str(caught.exception))


class TestTheRecoveryKeyNeverReachesTheRecord(unittest.TestCase):
    """The install trace is kept and read later; a key in it is a key on disk."""

    def test_the_layout_written_to_the_trace_carries_the_fact_not_the_key(self):
        events = []

        def fake_event(name, **fields):
            events.append((name, fields))

        layout = {"esp": "/dev/sda1", "root": "/dev/sda2", "luks_enabled": True,
                  "recovery_key": "AAAAA-BBBBB-CCCCC"}

        with patch.object(disks.trace, "trace_event", side_effect=fake_event), \
             patch.object(disks, "_disk_size_bytes", return_value=10 ** 12), \
             patch.object(disks, "_partition_disk", return_value=layout):
            returned = disks.partition_disk("/dev/sda", efi=True, luks_enabled=True,
                                            luks_passphrase="x",
                                            recovery_key_enabled=True)

        self.assertEqual(returned["recovery_key"], "AAAAA-BBBBB-CCCCC",
                         "the frontend still needs the key to show it once")
        ends = [fields for name, fields in events if name == "disk_phase_end"]
        self.assertEqual(len(ends), 1)
        traced = ends[0]["layout"]
        self.assertNotIn("recovery_key", traced)
        self.assertTrue(traced["recovery_key_present"])
        self.assertNotIn("AAAAA-BBBBB-CCCCC", repr(events))


class TestTheChoiceReachesTheInstaller(unittest.TestCase):
    """The graphical installer's state is what the backend actually reads."""

    def _state(self, **kwargs):
        from installer.frontend.gui.state import InstallerState
        state = InstallerState()
        for key, value in kwargs.items():
            setattr(state, key, value)
        return state

    def test_the_choice_is_threaded_when_the_disk_is_encrypted(self):
        state = self._state(luks_enabled=True, luks_passphrase="x",
                            luks_recovery_key_enable=True)
        self.assertTrue(state.to_install_io().get("luks_recovery_key_enable"))

    def test_it_is_not_threaded_on_an_unencrypted_install(self):
        state = self._state(luks_enabled=False, luks_recovery_key_enable=True)
        self.assertNotIn("luks_recovery_key_enable", state.to_install_io())

    def test_the_key_survives_the_credential_scrub_that_runs_before_it_is_shown(self):
        """The scrub drops passwords; the key is what the person must read."""
        state = self._state(luks_enabled=True, luks_passphrase="x",
                            luks_recovery_key="AAAAA-BBBBB")
        state.clear_sensitive_data()
        self.assertEqual(state.luks_recovery_key, "AAAAA-BBBBB")


class TestTheCompletionPageShowsItOnce(unittest.TestCase):

    def setUp(self):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gdk, Gtk
        Gtk.init_check()
        if Gdk.Display.get_default() is None:
            raise unittest.SkipTest("no display for GTK widget construction")

    def _description(self, **state_kwargs):
        from installer.frontend.gui.state import InstallerState
        from installer.frontend.gui.screens import done as done_screen

        state = InstallerState()
        state.install_completed = True
        state.install_failed = False
        for key, value in state_kwargs.items():
            setattr(state, key, value)

        page = done_screen.DonePage.__new__(done_screen.DonePage)
        page._status = __import__("gi").repository.Adw.StatusPage()
        page.next_button = __import__("gi").repository.Gtk.Button()
        page.on_load(state)
        return page._status.get_description()

    def test_the_key_is_on_the_page_when_one_was_generated(self):
        text = self._description(luks_recovery_key="AAAAA-BBBBB-CCCCC")
        self.assertIn("AAAAA-BBBBB-CCCCC", text)
        self.assertIn("only time it is shown", text)

    def test_nothing_about_recovery_keys_appears_when_none_was_generated(self):
        text = self._description()
        self.assertNotIn("RECOVERY KEY", text)


if __name__ == "__main__":
    unittest.main()
