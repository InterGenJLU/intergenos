# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The greeter monitor sync copies the layout bytes it checked, at a mode no wider
than the greeter needs.

igos-greeter-monitors-sync runs as root whenever a user's
~/.config/monitors.xml changes and copies it into GDM's seat state. It checked
the file by its path (a link test, a regular-file test, its owner and size,
an XML parse) and then opened the path again with cat to copy it, so a name
swapped for a link after the checks copied whatever the link named — any file
root can read — into the seat state, 0644, after widening the seat config
directory to 0755 on the way.

These cases run the helper from this tree as the user, as a copy whose
/var/lib/gdm is a scratch directory, with getent answered for one stand-in
account whose home is scratch too; nothing runs as root. The swap is made by
inotify at the moment the helper first opens the layout, whatever program does
the opening, so it lands after every check that precedes that open.
"""

import ctypes
import os
import select
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "packages/desktop/gdm/igos-greeter-monitors-sync"
LAYOUT = b'<monitors version="2"><configuration><layoutmode>logical</layoutmode></configuration></monitors>\n'
STAND_IN = b"<secret>a file only root may read</secret>\n"

_IN_OPEN = 0x00000020
_IN_CLOEXEC = 0o2000000
_libc = ctypes.CDLL(None, use_errno=True)


class _FirstOpen:
    """An inotify watch that reports the first open of one file."""

    def __init__(self, path: Path):
        self.fd = _libc.inotify_init1(_IN_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        if _libc.inotify_add_watch(self.fd, os.fsencode(path), _IN_OPEN) < 0:
            err = ctypes.get_errno()
            os.close(self.fd)
            raise OSError(err, "inotify_add_watch")

    def wait(self, timeout: float) -> bool:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if ready:
            os.read(self.fd, 4096)
        return bool(ready)

    def close(self) -> None:
        os.close(self.fd)


class TheGreeterSyncCopiesTheBytesItChecked(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "home"
        (self.home / ".config").mkdir(parents=True)
        self.layout = self.home / ".config" / "monitors.xml"
        self.layout.write_bytes(LAYOUT)
        self.stand_in = root / "elsewhere" / "secret.xml"
        self.stand_in.parent.mkdir()
        self.stand_in.write_bytes(STAND_IN)
        self.gdm = root / "gdm"
        self.seat_config = self.gdm / "seat0" / "config"
        self.target = self.seat_config / "monitors.xml"
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn("/var/lib/gdm", text)
        self.helper = root / "igos-greeter-monitors-sync"
        self.helper.write_text(text.replace("/var/lib/gdm", str(self.gdm)), encoding="utf-8")
        self.bin = root / "bin"
        self.bin.mkdir()
        self.uid = os.getuid()

    def tearDown(self):
        self._tmp.cleanup()

    def answer_getent_with(self, uid: int) -> None:
        getent = self.bin / "getent"
        getent.write_text(
            "#!/bin/sh\n"
            f'[ "$1" = passwd ] && [ "$2" = tester ] && '
            f'echo "tester:x:{uid}:{os.getgid()}:tester:{self.home}:/bin/sh" && exit 0\n'
            "exit 2\n", encoding="utf-8")
        getent.chmod(0o755)

    def run_helper(self, *, swap_on_first_open: bool = False):
        """Run the helper for the stand-in account; with swap_on_first_open the
        layout's name is replaced by a link to the stand-in the moment the
        helper first opens it. Returns (exit code, stdout, stderr, swapped)."""
        if not (self.bin / "getent").exists():
            self.answer_getent_with(self.uid)
        env = {"PATH": f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
               "LANG": "C.UTF-8", "HOME": str(self.home)}
        watch = _FirstOpen(self.layout) if swap_on_first_open else None
        swapped = False
        try:
            proc = subprocess.Popen(["bash", str(self.helper), "tester"], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if watch is not None and watch.wait(20):
                link = self.layout.with_name(".swap")
                os.symlink(self.stand_in, link)
                os.replace(link, self.layout)
                swapped = True
            out, err = proc.communicate(timeout=60)
        finally:
            if watch is not None:
                watch.close()
        return proc.returncode, out.decode(), err.decode(), swapped

    def test_control_an_unchanged_layout_is_copied(self):
        code, out, err, _ = self.run_helper()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.target.read_bytes(), LAYOUT)

    def test_a_layout_swapped_for_a_link_after_it_is_opened_does_not_land_the_links_target(self):
        code, out, err, swapped = self.run_helper(swap_on_first_open=True)
        self.assertTrue(swapped, "the helper never opened the layout")
        self.assertTrue(self.layout.is_symlink())
        self.assertEqual(code, 0, err)
        landed = self.target.read_bytes()
        self.assertNotIn(b"secret", landed, "the link's target was copied into the seat state")
        self.assertEqual(landed, LAYOUT)

    def test_a_link_at_the_name_is_refused(self):
        self.layout.unlink()
        self.layout.symlink_to(self.stand_in)
        code, out, err, _ = self.run_helper()
        self.assertEqual(code, 1)
        self.assertIn("is a symlink", err)
        self.assertFalse(self.target.exists())

    def test_a_layout_another_account_owns_is_refused(self):
        self.answer_getent_with(self.uid + 1)
        code, out, err, _ = self.run_helper()
        self.assertEqual(code, 1)
        self.assertIn(f"owned by uid {self.uid}, expected {self.uid + 1}", err)
        self.assertFalse(self.target.exists())

    def test_a_layout_that_is_not_xml_is_refused(self):
        self.layout.write_bytes(b"not a layout")
        code, out, err, _ = self.run_helper()
        self.assertEqual(code, 1)
        self.assertIn("not well-formed XML", err)
        self.assertFalse(self.target.exists())

    def test_the_copy_is_0600_and_an_existing_seat_config_directory_keeps_its_mode(self):
        self.seat_config.mkdir(parents=True)
        self.seat_config.chmod(0o700)
        code, out, err, _ = self.run_helper()
        self.assertEqual(code, 0, err)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.seat_config.stat().st_mode), 0o700)

    def test_an_absent_seat_config_directory_is_created_0700(self):
        code, out, err, _ = self.run_helper()
        self.assertEqual(code, 0, err)
        self.assertEqual(stat.S_IMODE(self.seat_config.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
