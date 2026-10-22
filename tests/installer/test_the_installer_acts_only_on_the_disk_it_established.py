# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The installer acts only on the disk it established, never on the name again.

The installer backend's bus check establishes the disk StartInstall names from
the object itself (installer/backend/disks.py establish_target_disk: a
root-owned block device node directly under /dev) and returns its identity
(st_dev, st_ino, st_rdev). That identity was dropped where it was returned:
the install worker and run_install named the disk again by its path, in the
disk phase (wipefs, parted, mkfs) and at the bootloader install, so an object
put at the same name between the check and those acts (a node the kernel
replaced on a hotplug) was the one written.

These tests drive the service's own method handler and the real install
pipeline with a scratch name standing in for the device node. The identity
the check returns is the identity the name's object has at that moment, read
the way the check reads it (the fstat of an O_PATH | O_NOFOLLOW descriptor),
and the replacement is another object renamed over the name. Every process the
pipeline would start is recorded and none is started.
  - The identity the bus check established travels into the worker's
    run_install.
  - A replacement after the check is refused in words before the partition
    phase acts; one between two disk-phase acts is refused at the next act;
    one after the disk phase is refused before the target is mounted and
    before the bootloader is installed. Each refusal names the step and what
    is at the name now.
  - A caller that hands run_install no identity (the TUI) has the disk
    established when the run starts, and the disk phase refuses to act on a
    disk whose identity nobody established.
  - A dry run is unchanged: it acts on nothing, so it checks nothing.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from gi.repository import GLib

from installer import backend_service
from installer.backend import _trace
from installer.backend import disks as backend_disks
from installer.backend import install as backend_install

CALLER = ":1.4242"
YAML = ("version: 1\nlocale: en_US.UTF-8\ntimezone: UTC\nhostname: intergenos\n"
        "package_groups: [core]\n")
NOT_THE_DISK = "is not the disk that was checked"


def _identity(path):
    """The identity the disk check returns for the object at `path` now."""
    fd = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    return (st.st_dev, st.st_ino, st.st_rdev)


class _Processes:
    """Replaces subprocess.Popen: records each command and starts nothing (rc
    0, empty output). A hook keyed by a command's first word runs once, right
    after that command is recorded."""

    def __init__(self):
        self.commands = []
        self.hooks = {}
        processes = self

        class _Popen:
            def __init__(self, args, *a, **kw):
                argv = [str(x) for x in (args if isinstance(args, (list, tuple))
                                         else ["sh", "-c", str(args)])]
                processes.commands.append(argv)
                hook = processes.hooks.pop(argv[0], None)
                if hook is not None:
                    hook()
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

    def first_words(self):
        return [argv[0] for argv in self.commands]


def _refuse_exec(*args, **kwargs):
    raise AssertionError(f"a process start outside subprocess.Popen: {args!r}")


class _Invocation:
    """Stands in for the Gio.DBusMethodInvocation: keeps the one answer."""

    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = (name, message)


class _Authorized:
    """Stands in for polkit: authorizes every acting call."""

    def check(self, sender, action_id, on_result):
        on_result(True, None)


class _DiskCase(unittest.TestCase):
    """A scratch root for the machine (the medium's dev marker under its
    /install), a scratch name for the disk with an object at it, another
    object ready to be renamed over it, and every process recorded and none
    started. The host reads that would stop the pipeline on a machine that is
    not the medium are stood in: the pre-flights (binaries, archives, the
    install set, the boot chain), the shim check, the firmware probe (EFI), the
    disk's size and cryptsetup's presence."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "install").mkdir()
        (self.root / "install" / "IGOS_DEV_ALLOW_UNVERIFIED").write_text("dev marker\n")
        devdir = self.root / "dev"
        devdir.mkdir()
        self.disk = str(devdir / "vdz")
        Path(self.disk).write_text("the object the check established\n")
        self.replacement = devdir / "vdz-replacement"
        self.replacement.write_text("another object\n")
        self.target = self.root / "mnt-target"
        self.target.mkdir()
        self.yaml_path = self.root / "install.yaml"
        self.yaml_path.write_text(YAML)
        self.processes = _Processes()
        patches = [
            mock.patch.object(subprocess, "Popen", self.processes.popen),
            mock.patch.object(backend_service, "DEFAULT_YAML_DIR", str(self.root / "var-lib-forge")),
            mock.patch.object(_trace.module, "_VERBOSE", False),
            mock.patch.object(backend_disks, "_DRY_RUN", False),
            mock.patch.object(backend_install, "preflight_check_binaries", lambda *a, **kw: None),
            mock.patch.object(backend_install, "preflight_check_archive_availability",
                              lambda *a, **kw: None),
            mock.patch.object(backend_install, "preflight_check_install_set_complete",
                              lambda *a, **kw: None),
            mock.patch.object(backend_install, "preflight_check_efi_boot_chain", lambda *a, **kw: None),
            mock.patch.object(backend_install.bootloader, "verify_shim_assets_present",
                              lambda *a, **kw: None),
            mock.patch.object(backend_disks, "is_efi", lambda: True),
            mock.patch.object(backend_disks, "_disk_size_bytes", lambda path: 10 ** 12),
            mock.patch.object(backend_disks, "cryptsetup_available", lambda: True),
        ]
        for name in ("system", "execv", "execve", "execvp", "execvpe",
                     "spawnv", "spawnve", "spawnvp", "spawnvpe", "posix_spawn", "posix_spawnp"):
            if hasattr(os, name):
                patches.append(mock.patch.object(os, name, _refuse_exec))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def swap(self):
        """Another object is at the disk's name now, as when the kernel
        replaces a node under the same name."""
        os.rename(self.replacement, self.disk)

    def install_io(self, **extra):
        return dict({"disk": self.disk, "username": "person", "user_password": "pw1234",
                     "root_password": "root1234", "mok_key_passphrase": "a-signing-passphrase"},
                    **extra)

    def assert_refused_at(self, result, step):
        message = result.error_message or ""
        self.assertFalse(result.success, "the install reported success")
        self.assertIn(step, message, message)
        self.assertIn(NOT_THE_DISK, message, message)
        self.assertIn(self.disk, message, message)


class _BusCase(_DiskCase):
    """The service on the scratch root with an authorizing polkit and a
    recording bus. The bus check is the service's own call, stood in to return
    the identity the name's object has when it is called (the real check takes
    only a root-owned block device directly under /dev); `after_check` runs
    right after it returns. The worker runs the real pipeline against the
    scratch target and keeps its result."""

    def setUp(self):
        super().setUp()
        self.checked = []
        self.after_check = None
        self.results = []
        self.run_kwargs = []
        real_run_install = backend_install.run_install

        def check(path, *a, **kw):
            identity = _identity(path)
            self.checked.append(identity)
            if self.after_check is not None:
                self.after_check()
            return identity

        def run_install(**kw):
            self.run_kwargs.append(dict(kw))
            result = real_run_install(**dict(kw, target=str(self.target)))
            self.results.append(result)
            return result

        for p in (mock.patch.object(backend_service, "establish_target_disk", check, create=True),
                  mock.patch.object(backend_service.backend_install, "run_install", run_install)):
            p.start()
            self.addCleanup(p.stop)
        self.service = backend_service.ForgeInstallerService(
            archive_dir=str(self.root / "archives"), packages_dir=str(self.root / "packages"))
        self.service._medium_root = self.root
        self.service._authority = _Authorized()
        self.service._bus = mock.Mock(emit_signal=lambda *a: None)
        self.addCleanup(self._join_worker)

    def _join_worker(self):
        worker = self.service._worker_thread
        if worker is not None:
            worker.join(timeout=120)
            self.assertFalse(worker.is_alive(), "the install worker did not finish")

    def start(self):
        invocation = _Invocation()
        install_io = {k: GLib.Variant("s", v) for k, v in self.install_io().items()}
        params = GLib.Variant("(sa{sv}b)", (YAML, install_io, False))
        self.service._on_method_call(None, CALLER, backend_service.OBJECT_PATH,
                                     backend_service.INTERFACE, "StartInstall", params, invocation)
        self.assertIsNone(invocation.error, invocation.error)
        self._join_worker()
        self.assertEqual(len(self.results), 1, "the worker ran no install")
        return self.results[0]


class TheIdentityTheBusCheckEstablishedTravelsIntoTheWorker(_BusCase):

    def test_run_install_receives_the_identity_the_check_returned(self):
        with mock.patch.object(backend_disks, "partition_disk",
                               side_effect=RuntimeError("stop before the disk phase acts")):
            self.start()
        self.assertEqual(len(self.checked), 1, "the bus check did not run once")
        self.assertEqual(self.run_kwargs[0].get("disk_identity"), self.checked[0],
                         "the worker's run_install was not handed the identity the bus check established")


class AReplacementAfterTheBusCheckIsRefused(_BusCase):

    def test_nothing_acts_on_the_replacement_and_the_refusal_names_the_partition_phase(self):
        self.after_check = self.swap
        result = self.start()
        self.assertEqual(self.processes.commands, [],
                         f"a process ran on the replaced name: {self.processes.commands}")
        self.assert_refused_at(result, "the partition phase")
        self.assertIn("a regular file", result.error_message)

    def test_the_same_object_proceeds_through_the_disk_phase(self):
        # The control: no replacement, so the check passes at every act.
        with mock.patch.object(backend_disks, "mount_target",
                               side_effect=RuntimeError("stop at the mount")) as mount:
            result = self.start()
        firsts = self.processes.first_words()
        for word in ("wipefs", "parted", "mkfs.fat", "mkfs.ext4"):
            self.assertIn(word, firsts, f"{word} did not run on the established disk: {firsts}")
        mount.assert_called_once()
        self.assertNotIn(NOT_THE_DISK, result.error_message or "")


class AReplacementBetweenTwoDiskPhaseActsIsRefusedAtTheNext(_BusCase):

    def test_the_partition_table_is_not_written_to_the_replacement_after_the_wipe(self):
        self.processes.hooks["wipefs"] = self.swap
        result = self.start()
        firsts = self.processes.first_words()
        self.assertEqual(firsts[-1], "wipefs",
                         f"an act ran after the name was replaced: {firsts}")
        self.assertNotIn("parted", firsts)
        self.assert_refused_at(result, "writing the partition table")


class AReplacementAfterTheDiskPhaseIsRefusedAtTheMount(_BusCase):

    def test_the_target_is_not_mounted_from_the_replacement(self):
        self.processes.hooks["mkfs.ext4"] = self.swap
        with mock.patch.object(backend_disks, "mount_target",
                               side_effect=RuntimeError("the target was mounted")) as mount:
            result = self.start()
        mount.assert_not_called()
        self.assert_refused_at(result, "mounting the target")


class AReplacementBeforeTheBootloaderIsRefusedThere(_BusCase):
    """The phases between the mount and the bootloader are stood in, as the
    orchestrator's own tests stand them in; the disk phase and the checks are
    real. The name is replaced while the packages install."""

    def test_the_bootloader_is_not_installed_on_the_replacement(self):
        def packages_installed(*a, **kw):
            self.swap()
            return (1, 0, [], ["linux-kernel"])

        stood_in = {}
        for name in ("packages", "config", "users", "mok", "hooks", "bootloader", "restorepoint"):
            if hasattr(backend_install, name):
                p = mock.patch.object(backend_install, name)
                stood_in[name] = p.start()
                self.addCleanup(p.stop)
        stood_in["packages"].install_packages.side_effect = packages_installed
        stood_in["packages"].compute_install_set_gap.return_value = []
        stood_in["packages"].efi_boot_chain_gap.return_value = []
        stood_in["mok"].generate_mok_keypair.return_value = {
            "key_path": "k", "cert_path": "c", "der_path": "d"}
        with mock.patch.object(backend_disks, "mount_target"), \
                mock.patch.object(backend_disks, "unmount_target", return_value=[]):
            result = self.start()
        stood_in["packages"].install_packages.assert_called_once()
        stood_in["bootloader"].install_bootloader.assert_not_called()
        self.assert_refused_at(result, "the bootloader install")


class ACallerWithNoIdentityHasTheDiskEstablishedFirst(_DiskCase):
    """run_install as the TUI calls it: no identity handed in."""

    def run_install(self, **kw):
        return backend_install.run_install(
            str(self.yaml_path), self.install_io(), None, None,
            target=str(self.target), **kw)

    def test_a_name_that_is_not_a_device_node_under_dev_is_refused_before_anything_acts(self):
        result = self.run_install()
        self.assertEqual(self.processes.commands, [],
                         f"a process ran on a disk nobody established: {self.processes.commands}")
        self.assertFalse(result.success)
        self.assertIn("is not a device node directly under /dev", result.error_message or "")

    def test_a_replacement_after_the_start_is_refused_at_the_partition_phase(self):
        established = []

        def establish(path, *a, **kw):
            identity = _identity(path)
            established.append(identity)
            self.swap()
            return identity

        with mock.patch.object(backend_disks, "establish_target_disk", establish, create=True):
            result = self.run_install()
        self.assertEqual(len(established), 1, "the run did not establish the disk")
        self.assertEqual(self.processes.commands, [],
                         f"a process ran on the replaced name: {self.processes.commands}")
        self.assert_refused_at(result, "the partition phase")

    def test_the_disk_phase_refuses_a_disk_whose_identity_nobody_established(self):
        try:
            backend_disks.partition_disk(self.disk, efi=True)
        except Exception as exc:  # the refusal is read below, by its type and words
            refusal = exc
        else:
            refusal = None
        self.assertEqual(self.processes.commands, [],
                         f"the disk phase acted on a disk nobody established: {self.processes.commands}")
        self.assertEqual(type(refusal).__name__, "TargetDiskRefused", repr(refusal))
        self.assertIn("established", str(refusal))


class ADryRunIsUnchanged(_DiskCase):
    """A dry run acts on nothing, so it neither establishes nor re-checks the
    disk: the preview is the one it was."""

    def test_the_dry_run_logs_the_disk_phase_and_checks_nothing(self):
        calls = []
        with mock.patch.object(backend_disks, "establish_target_disk",
                               lambda *a, **kw: calls.append(("establish", a)), create=True), \
                mock.patch.object(backend_disks, "confirm_target_disk",
                                  lambda *a, **kw: calls.append(("confirm", a)), create=True):
            self.swap()
            result = backend_install.run_install(
                str(self.yaml_path), self.install_io(), None, None,
                dry_run=True, target=str(self.target))
        backend_disks.set_dry_run(False)
        self.assertEqual(self.processes.commands, [], self.processes.commands)
        self.assertEqual(calls, [])
        self.assertEqual(result.phase_completed, backend_install.PHASE_PARTITION)
        self.assertFalse(result.success)


class TheRefusalSaysWhatIsAtTheNameNow(_DiskCase):
    """The words a refusal carries: the step, the name, the disk checked by
    its device numbers, and what is there now; never more than that."""

    def _refusal(self, path, identity, step):
        with self.assertRaises(backend_disks.TargetDiskChanged) as refused:
            backend_disks.confirm_target_disk(path, identity, step)
        return str(refused.exception)

    def test_the_words_for_another_object_a_block_device_and_nothing(self):
        if not hasattr(backend_disks, "confirm_target_disk"):
            self.fail("the disk phase has no check of the disk it established")
        checked = (1, 2, os.makedev(8, 16))
        text = self._refusal(self.disk, checked, "wiping the partition table")
        self.assertIn("wiping the partition table was refused", text)
        self.assertIn(f"{self.disk} {NOT_THE_DISK}", text)
        self.assertIn("device 8:16", text)
        self.assertIn("a regular file", text)
        # A block device node of this machine, only read (an O_PATH descriptor's
        # fstat): the words name its device numbers.
        node = next((e.path for e in sorted(os.scandir("/dev"), key=lambda e: e.name)
                     if stat.S_ISBLK(os.lstat(e.path).st_mode)), None)
        if node is not None:
            st = os.lstat(node)
            text = self._refusal(node, checked, "the partition phase")
            self.assertIn(f"a block device {os.major(st.st_rdev)}:{os.minor(st.st_rdev)}", text)
        os.unlink(self.disk)
        text = self._refusal(self.disk, checked, "the bootloader install")
        self.assertIn("nothing is at that name now", text)
        # The same object passes, and the check acts on nothing itself.
        backend_disks.confirm_target_disk(str(self.replacement), _identity(str(self.replacement)),
                                          "the partition phase")
        self.assertEqual(self.processes.commands, [])


if __name__ == "__main__":
    unittest.main()
