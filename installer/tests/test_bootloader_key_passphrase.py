# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Both install-time signing steps carry the owner's passphrase, and neither leaks it.

There are two of them, and the second was not in the report that started this work —
it was found by reading installer/backend/bootloader.py rather than by taking the
first one's word for the count:

  1. the boot loader is signed once (bootloader.py, via mok.sign_efi_binary);
  2. every /boot/vmlinuz-* image is signed in a loop further down the same function.

Both now need the passphrase, because the key they use is encrypted. The measured
constraint that shapes the second one: the boot-image signer reads ONE LINE from its
standard input per invocation, so a shell loop that runs it four times with a single
feed would sign the first image and hang or fail on the rest. The loop is therefore
driven from Python, one invocation and one feed per image.
"""

import unittest
from unittest import mock

from installer.backend import bootloader, mok


PASSPHRASE = "owner-passphrase-1"
KEYPAIR = {
    "key_path": "/var/lib/intergen/mok/mok.key",
    "cert_path": "/var/lib/intergen/mok/mok.crt",
    "der_path": "/var/lib/intergen/mok/mok.der",
}


class TestSignEfiBinaryCarriesThePassphrase(unittest.TestCase):

    def test_passphrase_goes_on_stdin_not_in_the_command(self):
        with mock.patch.object(mok, "run_chroot_stdin",
                               return_value=(0, "", "")) as runner:
            mok.sign_efi_binary(
                "/mnt/target", "/boot/efi/EFI/InterGenOS/grubx64.efi",
                KEYPAIR["key_path"], KEYPAIR["cert_path"],
                passphrase=PASSPHRASE)
        self.assertEqual(runner.call_count, 1)
        _target, command, stdin_data = runner.call_args[0][:3]
        self.assertNotIn(PASSPHRASE, command,
                         "the passphrase was put in the command text")
        self.assertEqual(stdin_data, PASSPHRASE + "\n",
                         "the signer reads exactly one line from stdin")

    def test_signing_without_a_passphrase_is_refused(self):
        with self.assertRaises(ValueError):
            mok.sign_efi_binary(
                "/mnt/target", "/boot/x.efi",
                KEYPAIR["key_path"], KEYPAIR["cert_path"], passphrase="")

    def test_a_failed_signature_raises(self):
        with mock.patch.object(mok, "run_chroot_stdin",
                               return_value=(1, "", "bad decrypt")):
            with self.assertRaises(RuntimeError):
                mok.sign_efi_binary(
                    "/mnt/target", "/boot/x.efi",
                    KEYPAIR["key_path"], KEYPAIR["cert_path"],
                    passphrase=PASSPHRASE)


class TestKernelImageSigningFeedsEachInvocation(unittest.TestCase):
    """One signer invocation and one passphrase feed per kernel image."""

    def test_each_image_is_signed_by_its_own_invocation(self):
        images = ["/boot/vmlinuz-6.18.10-igos-21", "/boot/vmlinuz-6.18.9-igos-20"]
        signed = []

        def fake_sign(target, binary_path, key_path, cert_path,
                      output_path=None, passphrase=None):
            self.assertEqual(passphrase, PASSPHRASE)
            signed.append(binary_path)
            return output_path or binary_path

        with mock.patch.object(bootloader, "sign_efi_binary", fake_sign), \
             mock.patch.object(bootloader, "_kernel_images_on_target",
                               return_value=images), \
             mock.patch.object(bootloader.trace, "traced_run_chroot",
                               return_value=(0, "", "")), \
             mock.patch.object(bootloader, "verify_efi_signature",
                               return_value=False):
            bootloader._sign_kernel_images(
                "/mnt/target", KEYPAIR, PASSPHRASE)

        self.assertEqual(signed, images)

    def test_an_already_signed_image_is_left_alone(self):
        """Idempotent on an install retry, as the shell loop was."""
        with mock.patch.object(bootloader, "sign_efi_binary") as signer, \
             mock.patch.object(bootloader, "_kernel_images_on_target",
                               return_value=["/boot/vmlinuz-a"]), \
             mock.patch.object(bootloader, "verify_efi_signature",
                               return_value=True):
            bootloader._sign_kernel_images("/mnt/target", KEYPAIR, PASSPHRASE)
        signer.assert_not_called()


class TestInstallBootloaderRequiresThePassphrase(unittest.TestCase):

    def test_efi_install_without_a_passphrase_is_refused(self):
        with self.assertRaises(ValueError):
            bootloader.install_bootloader(
                "/mnt/target", "/dev/sda", {"efi": True, "esp": "/dev/sda1"},
                mok_keypair=KEYPAIR, mok_passphrase=None)


if __name__ == "__main__":
    unittest.main()
