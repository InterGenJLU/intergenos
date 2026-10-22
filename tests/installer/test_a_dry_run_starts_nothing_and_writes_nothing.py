# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A dry run starts nothing and writes nothing, whatever the person chose.

The TUI's --dry-run runs the install pipeline with the disk phase's dry-run
switch set: each step logs the command it would run and runs none. Two
primitives on that path never consulted the switch. With encryption and a
recovery key chosen, add_recovery_key_slot ran cryptsetup luksAddKey (a write
to a LUKS header) and cryptsetup open --test-passphrase on partition 2 of the
named disk, as it already was, after writing the generated key to a named
temporary file; and the dry run's result then carried that key and said a
second unlock slot had been added. With the experimental unlock methods
switched on, the enrollment made its mount point and key directories on the
live system.

These tests run the real run_install with dry_run set, once per disk-phase
choice, both firmware modes, with the experimental switch on as well as off.
Every process start is recorded and none is started; every directory made,
temporary file and file or descriptor opened for writing outside the test's
scratch directory is recorded and none is made or written there. Only host
reads that would stop a dry run on a machine that is not the medium are stood
in: the binaries pre-flight, the shim check, the firmware probe, the disk's
size and the tools' and devices' presence.
"""

import builtins
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.backend import _trace
from installer.backend import bootloader as backend_bootloader
from installer.backend import disks as backend_disks
from installer.backend import install as backend_install

YAML = ("version: 1\nlocale: en_US.UTF-8\ntimezone: UTC\nhostname: intergenos\n"
        "package_groups: [core]\n")
PASSPHRASE = "a long and unusual passphrase"
ENCRYPTED = {"luks_enabled": True, "luks_passphrase": PASSPHRASE}
CHOICES = (
    ("EFI, encrypted, a recovery key", True, dict(ENCRYPTED, luks_recovery_key_enable=True), False),
    ("BIOS, encrypted, a recovery key", False, dict(ENCRYPTED, luks_recovery_key_enable=True), False),
    ("EFI, encrypted, a recovery key, TPM2 and FIDO2 (the experimental switch on)", True,
     dict(ENCRYPTED, luks_recovery_key_enable=True, tpm2_enabled=True, fido2_enabled=True), True),
    ("EFI, encrypted", True, dict(ENCRYPTED), False),
    ("BIOS, encrypted", False, dict(ENCRYPTED), False),
    ("EFI, plain", True, {}, False),
    ("BIOS, plain", False, {}, False),
)
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


class _Acts:
    """Every act a dry run must not take, recorded as (kind, detail); none of
    them is taken outside the scratch directory."""

    def __init__(self, scratch):
        self.scratch = os.path.realpath(scratch)
        self.taken = []
        acts = self

        class _Popen:
            def __init__(self, args, *a, **kw):
                argv = args if isinstance(args, (list, tuple)) else ["sh", "-c", str(args)]
                acts.taken.append(("process", " ".join(str(x) for x in argv)))
                self.args = args
                self._text = bool(kw.get("text") or kw.get("universal_newlines")
                                  or kw.get("encoding") or kw.get("errors"))
                self.returncode = 0
                self.pid = 4242
                self.stdin = self.stdout = self.stderr = None

            def communicate(self, input=None, timeout=None):
                empty = "" if self._text else b""
                return empty, empty

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

            terminate = kill

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        self.popen = _Popen

    def inside(self, path):
        p = os.path.realpath(os.path.abspath(os.fsdecode(path)))
        return p == self.scratch or p.startswith(self.scratch + os.sep) or p == os.devnull

    def patches(self):
        real_open, real_os_open = builtins.open, os.open
        real_makedirs, real_mkdir = os.makedirs, os.mkdir
        real_ntf = tempfile.NamedTemporaryFile
        acts = self

        def opened(file, mode="r", *a, **kw):
            if (isinstance(file, (str, bytes, os.PathLike)) and any(c in mode for c in "wax+")
                    and not acts.inside(file)):
                acts.taken.append(("file write", os.fsdecode(file)))
                return real_open(os.devnull, mode, *a, **kw)
            return real_open(file, mode, *a, **kw)

        def os_opened(path, flags, *a, **kw):
            if flags & WRITE_FLAGS and not acts.inside(path):
                acts.taken.append(("descriptor write", os.fsdecode(path)))
                return real_os_open(os.devnull, os.O_WRONLY | os.O_CLOEXEC)
            return real_os_open(path, flags, *a, **kw)

        def made(real):
            def make(path, *a, **kw):
                if not acts.inside(path):
                    acts.taken.append(("directory", os.fsdecode(path)))
                    return None
                return real(path, *a, **kw)
            return make

        def temporary(*a, **kw):
            acts.taken.append(("temporary file", f"NamedTemporaryFile(delete={kw.get('delete', True)})"))
            return real_ntf(*a, **dict(kw, dir=acts.scratch))

        def refuse(*a, **kw):
            acts.taken.append(("process", f"a start outside subprocess.Popen: {a!r}"))
            raise AssertionError("a process start outside subprocess.Popen")

        found = [
            mock.patch.object(subprocess, "Popen", self.popen),
            mock.patch.object(builtins, "open", opened),
            mock.patch.object(io, "open", opened),
            mock.patch.object(os, "open", os_opened),
            mock.patch.object(os, "makedirs", made(real_makedirs)),
            mock.patch.object(os, "mkdir", made(real_mkdir)),
            mock.patch.object(tempfile, "NamedTemporaryFile", temporary),
        ]
        for name in ("system", "execv", "execve", "execvp", "execvpe", "spawnv", "spawnve",
                     "spawnvp", "spawnvpe", "posix_spawn", "posix_spawnp"):
            if hasattr(os, name):
                found.append(mock.patch.object(os, name, refuse))
        return found


class ADryRunStartsNothingAndWritesNothing(unittest.TestCase):

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.yaml_path = self.scratch / "install.yaml"
        self.yaml_path.write_text(YAML)
        self.addCleanup(backend_disks.set_dry_run, False)

    def dry_run(self, efi, extra, offered):
        acts = _Acts(self.scratch)
        progress = []
        target = tempfile.mkdtemp(dir=self.scratch)
        patches = acts.patches() + [
            mock.patch.object(_trace.module, "_VERBOSE", False),
            mock.patch.object(backend_install, "preflight_check_binaries", lambda *a, **kw: None),
            mock.patch.object(backend_bootloader, "verify_shim_assets_present", lambda *a, **kw: None),
            mock.patch.object(backend_disks, "is_efi", lambda: efi),
            mock.patch.object(backend_disks, "_disk_size_bytes", lambda path: 10 ** 12),
            mock.patch.object(backend_disks, "cryptsetup_available", lambda: True),
            mock.patch.object(backend_disks, "tpm2_tools_available", lambda: True),
            mock.patch.object(backend_disks, "tpm2_present", lambda: True),
            mock.patch.object(backend_disks, "fido2_tools_available", lambda: True),
            mock.patch.object(backend_disks, "EXPERIMENTAL_UNLOCK_OFFERED", offered),
        ]
        install_io = dict({"disk": "/dev/vdz", "username": "person", "user_password": "pw1234",
                           "root_password": "root1234", "mok_key_passphrase": "a-signing-passphrase"},
                          **extra)
        for p in patches:
            p.start()
        try:
            result = backend_install.run_install(
                str(self.yaml_path), install_io, None, None, dry_run=True, target=target,
                progress_callback=lambda phase, cur, total, msg: progress.append(msg))
        finally:
            for p in reversed(patches):
                p.stop()
            backend_disks.set_dry_run(False)
        return result, acts.taken, progress

    def test_no_choice_makes_the_dry_run_start_or_write_anything(self):
        for name, efi, extra, offered in CHOICES:
            with self.subTest(choice=name):
                result, taken, _ = self.dry_run(efi, extra, offered)
                self.assertEqual(taken, [], f"{name}: the dry run acted")
                self.assertEqual(result.phase_completed, backend_install.PHASE_PARTITION,
                                 f"{name}: {result.error_message}")
                self.assertFalse(result.success)
                self.assertNotIn(PASSPHRASE, result.error_message or "")

    def test_a_dry_run_makes_no_recovery_key_and_says_no_slot_was_added(self):
        for name, efi, extra, offered in CHOICES:
            if not extra.get("luks_recovery_key_enable"):
                continue
            with self.subTest(choice=name):
                result, _, progress = self.dry_run(efi, extra, offered)
                self.assertEqual(result.luks_recovery_key, "",
                                 f"{name}: a dry run carried a recovery key")
                self.assertFalse([m for m in progress if "recovery key added" in m],
                                 f"{name}: a dry run said a slot was added: {progress}")


if __name__ == "__main__":
    unittest.main()
