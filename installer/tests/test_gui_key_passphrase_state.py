# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The graphical installer carries the signing key's passphrase the same way.

Same rules as the text installer, asserted at the state layer where they are
decided: an EFI install is not ready without it, it is not the disk passphrase,
it reaches the backend under the name the backend reads, and it is scrubbed from
memory with the other credentials when the install finishes.
"""

import unittest

from installer.frontend.gui.state import InstallerState


def efi_ready_state():
    state = InstallerState()
    state.target_disk = "/dev/sda"
    state.username = "owner"
    state.user_password = state.user_password_confirm = "user-pass-1"
    state.root_password = state.root_password_confirm = "root-pass-1"
    state.mok_key_passphrase = state.mok_key_passphrase_confirm = "signing-pass-1"
    return state


class TestItReachesTheBackend(unittest.TestCase):

    def test_it_is_emitted_under_the_name_the_backend_reads(self):
        io = efi_ready_state().to_install_io()
        self.assertEqual(io.get("mok_key_passphrase"), "signing-pass-1")

    def test_an_unset_passphrase_is_not_emitted_as_an_empty_string(self):
        """Absent and empty are different to the backend's validation."""
        state = efi_ready_state()
        state.mok_key_passphrase = ""
        self.assertNotIn("mok_key_passphrase", state.to_install_io())


class TestItIsTreatedAsACredential(unittest.TestCase):

    def test_it_is_scrubbed_with_the_others(self):
        state = efi_ready_state()
        state.clear_sensitive_data()
        self.assertEqual(state.mok_key_passphrase, "")
        self.assertEqual(state.mok_key_passphrase_confirm, "")


class TestTheDiskPassphraseIsNotReused(unittest.TestCase):

    def test_the_same_words_as_the_disk_passphrase_are_refused(self):
        state = efi_ready_state()
        state.luks_enabled = True
        state.luks_passphrase = state.luks_passphrase_confirm = "signing-pass-1"
        error = state.key_passphrase_error()
        self.assertIsNotNone(error)
        self.assertIn("disk", error.lower())

    def test_different_words_are_accepted(self):
        state = efi_ready_state()
        state.luks_enabled = True
        state.luks_passphrase = state.luks_passphrase_confirm = "disk-pass-1"
        self.assertIsNone(state.key_passphrase_error())

    def test_a_mismatched_confirmation_is_refused(self):
        state = efi_ready_state()
        state.mok_key_passphrase_confirm = "typed-differently"
        error = state.key_passphrase_error()
        self.assertIsNotNone(error)
        self.assertIn("match", error.lower())


if __name__ == "__main__":
    unittest.main()
