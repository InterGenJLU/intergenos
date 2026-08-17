# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""pkm deploy must UNLINK a running binary before overwriting it (ETXTBSY).

_safe_extract_tar → tarfile.extractall opens each destination regular file with
open(path, "wb") — writing IN PLACE. Overwriting a currently-EXECUTING binary
that way raises OSError ETXTBSY ("Text file busy"), which stranded the llama-cpp
r2 redeploy (2026-07-07). The deploy path now unlinks an existing regular file
first, so extractall lands the new content on a FRESH inode while the running
process keeps its old one. Every mainstream package manager does this.

RED (pre-fix): extracting over a running binary returns (False, "...Text file
busy..."). GREEN (with the unlink): returns (True, "") and the new content lands
while the process stays alive. A real running ELF (/bin/sleep) is used so the
ETXTBSY is genuine, not simulated.
"""
from __future__ import annotations

import errno
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

from pkm.installer import _safe_extract_tar, _unlink_existing_regular_file


def _tar_with_file(archive: Path, arcname: str, data: bytes, mode: int = 0o755):
    """Build a one-file .tar.gz whose sole member is a regular file."""
    src = archive.parent / "payload_src"
    src.write_bytes(data)
    os.chmod(src, mode)
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(src, arcname=arcname)
    src.unlink()


class UnlinkHelperTests(unittest.TestCase):
    """The helper is surgical: it unlinks ONLY existing regular files."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unlinks_regular_file(self):
        f = self.tmp / "f"
        f.write_text("x")
        _unlink_existing_regular_file(str(f))
        self.assertFalse(f.exists())

    def test_leaves_directory_intact(self):
        d = self.tmp / "d"
        d.mkdir()
        _unlink_existing_regular_file(str(d))
        self.assertTrue(d.is_dir())

    def test_leaves_symlink_intact(self):
        target = self.tmp / "t"
        target.write_text("x")
        link = self.tmp / "s"
        os.symlink("t", link)
        _unlink_existing_regular_file(str(link))
        self.assertTrue(os.path.islink(link), "symlink must be left to tarfile")
        self.assertTrue(target.exists(), "the symlink's target must be untouched")

    def test_missing_target_is_a_noop(self):
        _unlink_existing_regular_file(str(self.tmp / "nope"))  # must not raise


@unittest.skipUnless(sys.platform.startswith("linux"),
                     "ETXTBSY-on-write-to-running-binary is Linux behavior")
class DeployOverRunningBinaryTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dest = self.tmp / "root"
        self.dest.mkdir()
        self._procs: list[subprocess.Popen] = []

    def tearDown(self):
        for p in self._procs:
            try:
                p.send_signal(signal.SIGKILL)
                p.wait(timeout=2)
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _running_binary_at(self, relpath: str):
        """Copy a real ELF (/bin/sleep) to dest/relpath, start it running so it
        holds the file's text busy, and return (target_path, proc)."""
        real = shutil.which("sleep")
        self.assertIsNotNone(real, "need a real ELF (sleep) to hold ETXTBSY")
        target = self.dest / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(real, target)
        os.chmod(target, 0o755)
        proc = subprocess.Popen([str(target), "30"])
        self._procs.append(proc)
        for _ in range(100):          # wait until it is genuinely executing
            if proc.poll() is None:
                break
            time.sleep(0.02)
        self.assertIsNone(proc.poll(), "fixture binary failed to start")
        return target, proc

    def test_environment_really_produces_ETXTBSY(self):
        """Anchor: confirm this host DOES raise ETXTBSY on an in-place write to a
        running binary — the exact condition the fix defends against."""
        target, proc = self._running_binary_at("usr/bin/foo")
        with self.assertRaises(OSError) as ctx:
            with open(target, "wb") as fh:
                fh.write(b"clobber")
        self.assertEqual(ctx.exception.errno, errno.ETXTBSY)
        self.assertIsNone(proc.poll(), "the binary must still be running")

    def test_deploy_over_running_binary_succeeds_via_unlink(self):
        """GREEN: _safe_extract_tar redeploys new content over a RUNNING binary
        without ETXTBSY, because it unlinks the old inode first. (RED without the
        fix: extractall's in-place open raises ETXTBSY → returns (False, ...).)"""
        target, proc = self._running_binary_at("usr/bin/foo")
        new_content = b"#!/bin/sh\nexit 0\n"
        archive = self.tmp / "pkg.tar.gz"
        _tar_with_file(archive, "./usr/bin/foo", new_content, 0o755)

        ok, msg = _safe_extract_tar(str(archive), str(self.dest))

        self.assertTrue(ok, f"deploy over a running binary must succeed, got: {msg}")
        self.assertEqual(target.read_bytes(), new_content,
                         "the new content must land on the fresh inode")
        self.assertIsNone(proc.poll(),
                          "the running process keeps its old (now-unlinked) inode")


if __name__ == "__main__":
    unittest.main()
