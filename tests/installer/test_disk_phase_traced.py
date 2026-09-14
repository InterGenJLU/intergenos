# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The installer's disk phase is on the record (R001.3 row S1-28).

WHAT WAS WRONG (the hub workstation's install, 2026-09-05). The install trace
held `trace_init` and `run_install_entry`, then nothing for 23 seconds until
`target_sink_attached`: wipefs, parted, mkfs and cryptsetup — the one
destructive step of an install — ran through a bare subprocess.run in
installer/backend/disks.py and were recorded nowhere, so the target was
provable only from the outcome. And the durable copy of the trace (the file
under /var/log on the installed system) was opened only after the target was
mounted, so even a traced disk phase would have lived only in the live
session's /tmp and died with it at reboot.

WHAT THIS PROVES. (1) Every command the partition step issues — the holder
release, wipefs, the parted calls, partprobe, udevadm, mkfs, cryptsetup — is a
subprocess_start/subprocess_end pair in the trace with its argv, rc and
output; the phase opens and closes with a row that states the disk and the
resulting layout. (2) The LUKS passphrase fed to cryptsetup on stdin is
withheld with its byte count kept and appears nowhere in the sink, while the
tool itself still receives it. (3) A failing command is a row (rc, stderr) and
a disk_phase_failed row before it is an exception. (4) The mount and unmount
steps are rows, an unmount's non-zero rc included. (5) When the target sink
attaches, every row the live sink already holds is replayed into the target
file first and the attach row counts them. (6) The disk module has no bare
subprocess.run left (source assertion). (7) The BIOS and plain-ext4 paths are
covered the same way.

The subprocess layer under the trace writer is replaced by a recorder, so no
disk is touched; the writer, the disk module and the rows are real.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.backend import disks, trace

DISKS_PY = Path(disks.__file__).resolve()   # the module under test, wherever it lives

PASSPHRASE = "correct horse battery staple 2026"
DISK = "/dev/nvme9n9"
SIZE = 64 * 1024**3

_shared = trace.module  # the one igos_trace instance disks.py writes to


class _FakeRun:
    """Stands in for subprocess.run under the trace writer: records argv and
    the stdin it was handed, answers rc 0 unless told otherwise."""

    def __init__(self):
        self.calls = []
        self.fail = {}   # argv[0] (or a substring of the joined argv) -> (rc, stderr)

    def __call__(self, argv, **kw):
        joined = " ".join(map(str, argv))
        self.calls.append((list(argv), kw.get("input")))
        rc, err = 0, ""
        for key, (frc, ferr) in self.fail.items():
            if key in joined:
                rc, err = frc, ferr
        text = kw.get("text", False)
        out = ""
        if argv[0] == "lsblk" and "-rno" in argv:
            out = ""                       # no holders on the fake disk
        if text:
            return subprocess.CompletedProcess(list(argv), rc, out, err)
        return subprocess.CompletedProcess(list(argv), rc, out.encode(), err.encode())


class _Sink:
    """A 0600 sink under a temp dir on the shared writer, verbose forced on;
    restored on exit."""

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "live.jsonl"
        self._verbose = _shared._VERBOSE
        self._sinks = list(_shared._SINKS)
        _shared._VERBOSE = True
        _shared._SINKS[:] = [_shared._open_600(self.path)]
        return self

    def rows(self):
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def raw(self):
        return self.path.read_text()

    def __exit__(self, *a):
        for s in _shared._SINKS:
            try:
                s.close()
            except Exception:
                pass
        _shared._SINKS[:] = self._sinks
        _shared._VERBOSE = self._verbose
        shutil.rmtree(self.tmp, ignore_errors=True)


def _starts(rows):
    return [r for r in rows if r["type"] == "subprocess_start"]


def _ends(rows):
    return [r for r in rows if r["type"] == "subprocess_end"]


def _tools(rows):
    return [r["cmd"][0] for r in _starts(rows)]


class DiskPhaseTraced(unittest.TestCase):

    def _partition(self, fake, **kw):
        with patch.object(_shared.subprocess, "run", fake), \
             patch.object(disks, "_disk_size_bytes", lambda d: SIZE), \
             patch.object(disks, "cryptsetup_available", lambda: True):
            return disks.partition_disk(DISK, **kw)

    def test_efi_luks_every_command_is_a_row_and_the_passphrase_is_not(self):
        fake = _FakeRun()
        with _Sink() as sink:
            layout = self._partition(fake, efi=True, luks_enabled=True,
                                     luks_passphrase=PASSPHRASE)
            rows = sink.rows()
            raw = sink.raw()

        # The phase opens and closes with the layout on record.
        begin = [r for r in rows if r["type"] == "disk_phase_begin"]
        end = [r for r in rows if r["type"] == "disk_phase_end"]
        self.assertEqual(len(begin), 1)
        self.assertEqual(begin[0]["disk"], DISK)
        self.assertEqual(begin[0]["disk_size_bytes"], SIZE)
        self.assertTrue(begin[0]["luks_enabled"] and begin[0]["efi"])
        self.assertEqual(len(end), 1)
        self.assertEqual(end[0]["layout"], layout)
        self.assertEqual(layout["root_mapper"], "/dev/mapper/cryptroot")

        # Every command the fake ran is a start/end pair with the same argv.
        ran = [c for c, _ in fake.calls]
        self.assertEqual([r["cmd"] for r in _starts(rows)], ran)
        self.assertEqual([r["cmd"] for r in _ends(rows)], ran)
        self.assertTrue(all(r["rc"] == 0 for r in _ends(rows)))
        for r in _starts(rows):
            self.assertEqual(r["phase"], "partition")
            self.assertTrue(r["intent"])

        # The destructive sequence, in order (the holder release's own
        # `udevadm settle` comes first; it is a row too).
        tools = _tools(rows)
        expected = ["udevadm", "wipefs", "parted", "parted", "parted", "parted",
                    "partprobe", "udevadm", "mkfs.fat", "cryptsetup", "cryptsetup",
                    "mkfs.ext4"]
        self.assertEqual([t for t in tools if t not in ("lsblk", "pvs")], expected)
        # The holder release ran (lsblk listing + udevadm settle) before wipefs.
        self.assertLess(tools.index("lsblk"), tools.index("wipefs"))

        # cryptsetup got the passphrase; the trace did not.
        crypt = [(c, i) for c, i in fake.calls if c[0] == "cryptsetup"]
        self.assertEqual(len(crypt), 2)
        for c, i in crypt:
            self.assertEqual(i, PASSPHRASE.encode())
        crypt_rows = [r for r in _starts(rows) if r["cmd"][0] == "cryptsetup"]
        for r in crypt_rows:
            self.assertEqual(r["stdin"], "<REDACTED>")
            self.assertEqual(r["stdin_redacted"], "credential consumer: cryptsetup")
            self.assertEqual(r["stdin_bytes"], len(PASSPHRASE.encode()))
        self.assertNotIn(PASSPHRASE, raw)
        self.assertNotIn("correct horse", raw)
        # And the sink stays owner-only.
        self.assertEqual(os.stat(sink.path).st_mode & 0o777, 0o600) if sink.path.exists() else None

    def test_bios_plain_path_is_traced_too(self):
        fake = _FakeRun()
        with _Sink() as sink:
            layout = self._partition(fake, efi=False)
            rows = sink.rows()
        self.assertEqual(layout, {"bios_grub": f"{DISK}p1", "root": f"{DISK}p2", "efi": False})
        tools = [t for t in _tools(rows) if t not in ("lsblk", "pvs")]
        self.assertEqual(tools, ["udevadm", "wipefs", "parted", "parted", "parted", "parted",
                                 "partprobe", "udevadm", "mkfs.ext4"])
        self.assertEqual([r["type"] for r in rows if r["type"].startswith("disk_phase")],
                         ["disk_phase_begin", "disk_phase_end"])

    def test_a_failing_command_is_a_row_before_it_is_an_exception(self):
        fake = _FakeRun()
        fake.fail["mkfs.ext4"] = (1, "mkfs.ext4: /dev/nvme9n9p2 is apparently in use")
        with _Sink() as sink:
            with self.assertRaises(RuntimeError) as cm:
                self._partition(fake, efi=True)
            rows = sink.rows()
        self.assertIn("apparently in use", str(cm.exception))
        failed_end = [r for r in _ends(rows) if r["cmd"][0] == "mkfs.ext4"]
        self.assertEqual(len(failed_end), 1)
        self.assertEqual(failed_end[0]["rc"], 1)
        self.assertIn("apparently in use", failed_end[0]["stderr"])
        failed = [r for r in rows if r["type"] == "disk_phase_failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("mkfs.ext4", failed[0]["error"])
        self.assertFalse([r for r in rows if r["type"] == "disk_phase_end"])

    def test_luks_failure_row_carries_stderr_but_never_the_passphrase(self):
        fake = _FakeRun()
        fake.fail["luksFormat"] = (1, "Device /dev/nvme9n9p2 is not a valid LUKS device.")
        with _Sink() as sink:
            with self.assertRaises(RuntimeError):
                self._partition(fake, efi=True, luks_enabled=True,
                                luks_passphrase=PASSPHRASE)
            rows = sink.rows()
            raw = sink.raw()
        end = [r for r in _ends(rows) if "luksFormat" in r["cmd"]][0]
        self.assertEqual(end["rc"], 1)
        self.assertIn("not a valid LUKS device", end["stderr"])
        self.assertNotIn(PASSPHRASE, raw)
        self.assertEqual([r for r in rows if r["type"] == "disk_phase_failed"][0]["phase"],
                         "partition")

    def test_mount_and_unmount_are_rows_with_their_rc(self):
        fake = _FakeRun()
        fake.fail["umount /mnt/t/boot/efi"] = (32, "umount: /mnt/t/boot/efi: not mounted.")
        with _Sink() as sink, patch.object(_shared.subprocess, "run", fake), \
             patch.object(disks.os, "makedirs", lambda *a, **k: None):
            disks.mount_target({"root": f"{DISK}p2", "esp": f"{DISK}p1", "efi": True},
                               target="/mnt/t")
            disks.unmount_target("/mnt/t")   # never raises; results were discarded before
            rows = sink.rows()
        mounts = [r for r in _starts(rows) if r["cmd"][0] == "mount"]
        self.assertEqual([r["cmd"] for r in mounts],
                         [["mount", f"{DISK}p2", "/mnt/t"], ["mount", f"{DISK}p1", "/mnt/t/boot/efi"]])
        self.assertTrue(all(r["phase"] == "mount" for r in mounts))
        umounts = [r for r in _ends(rows) if r["cmd"][0] == "umount"]
        self.assertEqual(len(umounts), 7)
        self.assertTrue(all(r["phase"] == "cleanup" for r in umounts))
        esp = [r for r in umounts if r["cmd"][1] == "/mnt/t/boot/efi"][0]
        self.assertEqual(esp["rc"], 32)
        self.assertIn("not mounted", esp["stderr"])

    def test_target_sink_replays_the_live_rows_first(self):
        with _Sink() as sink:
            _shared._RUNID, _shared._START_TS = "abcdef0123456789", "20260914T190000Z"
            try:
                _shared._emit({"type": "run_install_entry", "install_id": "x"})
                _shared.trace_event("disk_phase_begin", phase="partition", disk=DISK)
                _shared.trace_event("disk_phase_end", phase="partition", disk=DISK,
                                    layout={"root": f"{DISK}p2"})
                target = sink.tmp / "target"
                _shared.attach_target_sink(target)
                _shared.trace_event("after_attach", phase="virtual_fs")
                target_file = target / "var/log/forge-install-20260914T190000Z-abcdef0123456789.log"
                self.assertTrue(target_file.exists())
                self.assertEqual(os.stat(target_file).st_mode & 0o777, 0o600)
                trows = [json.loads(l) for l in target_file.read_text().splitlines() if l.strip()]
                lrows = sink.rows()
            finally:
                _shared._RUNID = _shared._START_TS = None
        # The target copy carries the whole story: the three pre-mount rows,
        # then the attach row that counts them, then what came after.
        self.assertEqual([r["type"] for r in trows],
                         ["run_install_entry", "disk_phase_begin", "disk_phase_end",
                          "target_sink_attached", "after_attach"])
        self.assertEqual(trows[3]["replayed_rows"], 3)
        self.assertEqual(trows[2]["layout"], {"root": f"{DISK}p2"})
        # The live sink also got the attach row and the later one; no duplicates.
        self.assertEqual([r["type"] for r in lrows],
                         ["run_install_entry", "disk_phase_begin", "disk_phase_end",
                          "target_sink_attached", "after_attach"])

    def test_dry_run_leaves_a_row_saying_the_command_was_skipped(self):
        fake = _FakeRun()
        disks.set_dry_run(True)
        try:
            with _Sink() as sink:
                self._partition(fake, efi=True)
                rows = sink.rows()
        finally:
            disks.set_dry_run(False)
        self.assertFalse(fake.calls)                       # nothing ran
        skipped = [r for r in rows if r["type"] == "subprocess_dry_run"]
        self.assertEqual([r["cmd"][0] for r in skipped][:2], ["wipefs", "parted"])
        self.assertTrue([r for r in rows if r["type"] == "disk_phase_begin"][0]["dry_run"])

    def test_the_disk_module_has_no_bare_subprocess_call_left(self):
        src = DISKS_PY.read_text()
        self.assertEqual(len(re.findall(r"\bsubprocess\.(run|Popen|check_output|call)\(", src)), 0,
                         "a command in the disk module bypasses the trace")
        self.assertIn("from . import trace", src)


if __name__ == "__main__":
    unittest.main()
