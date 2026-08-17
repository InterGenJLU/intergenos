# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The root LUKS volume must not be detached while the root fs is mounted.

Measured on an FDE install (shutdown journal, 2026-07-31):

    systemd-cryptsetup[125027]: Device cryptroot is still in use.
    systemd-cryptsetup[125027]: Failed to deactivate 'cryptroot': Device or
        resource busy
    systemd-cryptsetup@cryptroot.service: Failed with result 'exit-code'.

Cause: without the crypttab(5) option "x-initrd.attach",
systemd-cryptsetup-generator emits `Conflicts=umount.target` +
`Before=umount.target` on the unit, so the shutdown transaction stops it —
running `systemd-cryptsetup detach` — before the root filesystem is
unmounted. Proven by running the generator against the same crypttab with
and without the option: those two lines are the entire difference.

These tests pin the option into every generated crypttab and pin the
properties fde-init.sh depends on (field count, name field, exact-token
unlock-method matching).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from installer.backend import config
from installer.backend.config import ROOT_CRYPT_SHUTDOWN_OPT, generate_crypttab


class _CrypttabWriterCase(unittest.TestCase):
    def setUp(self):
        self._orig_get_uuid = config._get_uuid
        config._get_uuid = lambda dev: "2ccf1b34-38aa-4f7f-b85d-7b1df989ae87"
        self.addCleanup(lambda: setattr(config, "_get_uuid",
                                        self._orig_get_uuid))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = self._tmp.name

    def _write(self, partitions):
        generate_crypttab(self.target, partitions)
        path = Path(self.target) / "etc" / "crypttab"
        return path

    def _options_field(self, path):
        for line in path.read_text().splitlines():
            if line.startswith("cryptroot"):
                return line.split()[3]
        self.fail("no cryptroot line in the generated crypttab")


class ShutdownDetachOptionTests(_CrypttabWriterCase):
    def test_plain_passphrase_install_carries_the_option(self):
        path = self._write({"luks_enabled": True, "root": "/dev/nvme0n1p2"})
        opts = self._options_field(path).split(",")
        self.assertIn(ROOT_CRYPT_SHUTDOWN_OPT, opts)
        self.assertEqual(opts[:2], ["luks", "discard"],
                         "the baseline options must keep their order")

    def test_experimental_unlock_tokens_are_preserved(self):
        path = self._write({
            "luks_enabled": True,
            "root": "/dev/nvme0n1p2",
            "crypt_opts": ["luks", "discard", "tpm2", "fido2"],
        })
        opts = self._options_field(path).split(",")
        # fde-init.sh matches ",tpm2," / ",fido2," against the comma-wrapped
        # options field — exact tokens, so an added option cannot enable or
        # disable an unlock method.
        self.assertIn("tpm2", opts)
        self.assertIn("fido2", opts)
        self.assertIn(ROOT_CRYPT_SHUTDOWN_OPT, opts)

    def test_option_is_not_duplicated_when_already_present(self):
        path = self._write({
            "luks_enabled": True,
            "root": "/dev/nvme0n1p2",
            "crypt_opts": ["luks", "discard", ROOT_CRYPT_SHUTDOWN_OPT],
        })
        opts = self._options_field(path).split(",")
        self.assertEqual(opts.count(ROOT_CRYPT_SHUTDOWN_OPT), 1)

    def test_caller_list_is_not_mutated(self):
        crypt_opts = ["luks", "discard"]
        self._write({"luks_enabled": True, "root": "/dev/nvme0n1p2",
                     "crypt_opts": crypt_opts})
        self.assertEqual(crypt_opts, ["luks", "discard"],
                         "generate_crypttab must not mutate the caller's list")

    def test_line_shape_stays_four_fields(self):
        path = self._write({"luks_enabled": True, "root": "/dev/nvme0n1p2"})
        line = [l for l in path.read_text().splitlines()
                if l.startswith("cryptroot")][0]
        fields = line.split()
        self.assertEqual(len(fields), 4,
                         "fde-init.sh awks fields 1, 2 and 4 — the shape is a "
                         "contract")
        self.assertEqual(fields[0], "cryptroot")
        self.assertTrue(fields[1].startswith("UUID="))
        self.assertEqual(fields[2], "none")

    def test_plain_install_still_gets_no_crypttab(self):
        generate_crypttab(self.target, {"luks_enabled": False,
                                        "root": "/dev/nvme0n1p2"})
        self.assertFalse((Path(self.target) / "etc" / "crypttab").exists())


if __name__ == "__main__":
    unittest.main()
