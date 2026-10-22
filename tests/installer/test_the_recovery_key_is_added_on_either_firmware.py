# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A recovery key the person asked for is added, and proven, on either firmware.

Both frontends offer a recovery key whenever the disk is encrypted, on EFI and
BIOS machines alike, and say it is shown once when the install finishes. The
disk phase added and proved the key slot only on its EFI branch: on a BIOS
machine an encrypted install with a recovery key chosen finished with no
second key slot and showed no key, and nothing said so. A key slot on a LUKS
volume does not depend on the firmware.

These tests run the real partition_disk, not a dry run, on each branch with
encryption and a recovery key chosen. Every process is recorded and none is
started; the disk exists on no machine, so its size, cryptsetup's presence
and the check of the disk's identity are stood in.
"""

import subprocess
import unittest
from unittest import mock

from installer.backend import _trace
from installer.backend import disks as backend_disks

PASSPHRASE = "a long and unusual passphrase"


class _Popen:
    """Records each command and starts nothing (rc 0, empty output)."""

    commands = []

    def __init__(self, args, *a, **kw):
        _Popen.commands.append([str(x) for x in args] if isinstance(args, (list, tuple)) else [str(args)])
        self.args, self.returncode, self.pid = args, 0, 4242
        self._text = bool(kw.get("text") or kw.get("universal_newlines")
                          or kw.get("encoding") or kw.get("errors"))
        self.stdin = self.stdout = self.stderr = None

    def communicate(self, input=None, timeout=None):
        empty = "" if self._text else b""
        return empty, empty

    def wait(self, timeout=None):
        return 0

    poll = wait

    def kill(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TheRecoveryKeyIsAddedOnEitherFirmware(unittest.TestCase):

    def partition(self, efi):
        _Popen.commands = []
        with mock.patch.object(subprocess, "Popen", _Popen), \
                mock.patch.object(_trace.module, "_VERBOSE", False), \
                mock.patch.object(backend_disks, "_DRY_RUN", False), \
                mock.patch.object(backend_disks, "_disk_size_bytes", lambda path: 10 ** 12), \
                mock.patch.object(backend_disks, "cryptsetup_available", lambda: True), \
                mock.patch.object(backend_disks, "confirm_target_disk", lambda *a: None):
            layout = backend_disks.partition_disk(
                "/dev/vdz", efi=efi, luks_enabled=True, luks_passphrase=PASSPHRASE,
                recovery_key_enabled=True, disk_identity=(0, 0, 0))
        return layout, _Popen.commands

    def assert_added_and_proven(self, efi):
        layout, commands = self.partition(efi)
        root = layout["root"]
        added = [c for c in commands if c[:2] == ["cryptsetup", "luksAddKey"]]
        proven = [c for c in commands if c[:3] == ["cryptsetup", "open", "--test-passphrase"]]
        self.assertEqual(len(added), 1, f"no key slot was added: {commands}")
        self.assertIn(root, added[0], "the slot was not added on the root partition")
        self.assertEqual(len(proven), 1, f"the new key was not proven: {commands}")
        self.assertEqual(proven[0][-1], root)
        self.assertGreater(commands.index(added[0]),
                           next(i for i, c in enumerate(commands) if c[:2] == ["cryptsetup", "luksFormat"]),
                           "the slot was added before the volume existed")
        self.assertRegex(layout.get("recovery_key", ""), r"^([2-9A-HJKMNP-Z]{5}-){7}[2-9A-HJKMNP-Z]{5}$",
                         "the layout carries no key to show the person once")
        self.assertNotIn(PASSPHRASE, " ".join(" ".join(c) for c in commands))

    def test_on_an_efi_machine(self):
        self.assert_added_and_proven(efi=True)

    def test_on_a_bios_machine(self):
        self.assert_added_and_proven(efi=False)


if __name__ == "__main__":
    unittest.main()
