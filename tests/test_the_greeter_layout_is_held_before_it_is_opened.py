# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The greeter's layout helpers hold what they act on, at the greeter's modes.

igos-greeter-monitors-sync runs as root over a file in a directory its user
controls. It opened the layout's name for reading and only then asked what the
name was, so a FIFO put there was opened (its writer let go) before the
refusal; and it reached the layout's directory by path, through any link on
the way. igos-greeter-monitors-seed widened an existing seat config directory
to 0755 and landed its copy at 0644, where the sync keeps the directory GDM
made and lands 0600.

These cases run the helpers from this tree as the user, as copies whose
/var/lib/gdm and /var/lib/igos are scratch directories, with getent answered
for one stand-in account whose home is scratch too; nothing runs as root.
"""

import os
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

GDM = Path(__file__).resolve().parents[1] / "packages/desktop/gdm"
LAYOUT = b'<monitors version="2"><configuration><layoutmode>logical</layoutmode></configuration></monitors>\n'


class _Scratch(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        (self.home / ".config").mkdir(parents=True)
        self.layout = self.home / ".config" / "monitors.xml"
        self.gdm = self.root / "gdm"
        self.seat_config = self.gdm / "seat0" / "config"
        self.target = self.seat_config / "monitors.xml"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        getent = self.bin / "getent"
        getent.write_text(
            "#!/bin/sh\n"
            f'[ "$1" = passwd ] && [ "$2" = tester ] && '
            f'echo "tester:x:{os.getuid()}:{os.getgid()}:tester:{self.home}:/bin/sh" && exit 0\n'
            "exit 2\n", encoding="utf-8")
        getent.chmod(0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def helper(self, name, replacements):
        text = (GDM / name).read_text(encoding="utf-8")
        for old, new in replacements:
            self.assertIn(old, text)
            text = text.replace(old, new)
        copy = self.root / name
        copy.write_text(text, encoding="utf-8")
        return copy

    def call(self, helper, *args, timeout=30):
        env = {"PATH": f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
               "LANG": "C.UTF-8", "HOME": str(self.home)}
        proc = subprocess.run(["bash", str(helper), *args], env=env,
                              capture_output=True, timeout=timeout)
        return proc.returncode, proc.stdout.decode(), proc.stderr.decode()


class TheSyncHoldsTheLayoutBeforeItOpensIt(_Scratch):

    def sync(self):
        return self.call(self.helper("igos-greeter-monitors-sync",
                                     [("/var/lib/gdm", str(self.gdm))]),
                         "tester")

    def test_control_a_layout_is_copied(self):
        self.layout.write_bytes(LAYOUT)
        code, _out, err = self.sync()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.target.read_bytes(), LAYOUT)

    def test_a_fifo_at_the_name_is_refused_without_being_opened(self):
        os.mkfifo(self.layout, 0o600)
        released = []

        def writer():
            fd = os.open(self.layout, os.O_WRONLY)
            released.append(True)
            os.close(fd)

        peer = threading.Thread(target=writer, daemon=True)
        peer.start()
        time.sleep(0.3)
        try:
            code, _out, err = self.sync()
        finally:
            peer.join(1.0)
            was_released = bool(released)
            if not was_released:
                mine = os.open(self.layout, os.O_RDWR | os.O_NONBLOCK)
                peer.join(2.0)
                os.close(mine)
        self.assertEqual(code, 1)
        self.assertIn("is not a regular file — refusing", err)
        self.assertFalse(was_released, "the sync opened the FIFO at the layout's name")
        self.assertFalse(self.target.exists())

    def test_a_directory_on_the_way_that_is_a_link_is_refused(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "monitors.xml").write_bytes(LAYOUT)
        os.rmdir(self.home / ".config")
        (self.home / ".config").symlink_to(elsewhere)
        code, _out, err = self.sync()
        self.assertEqual(code, 1)
        self.assertIn("is not a directory reached without a link", err)
        self.assertFalse(self.target.exists())


class TheSeedLandsAtTheGreetersModes(_Scratch):

    def seed(self):
        self.staged = self.root / "igos" / "greeter-monitors-seed.xml"
        self.staged.parent.mkdir()
        self.staged.write_bytes(LAYOUT)
        helper = self.helper("igos-greeter-monitors-seed",
                             [("/var/lib/igos/greeter-monitors-seed.xml",
                               str(self.staged)),
                              ("/var/lib/gdm", str(self.gdm))])
        return self.call(helper)

    def test_an_absent_seat_config_directory_is_created_0700_and_the_copy_is_0600(self):
        code, _out, err = self.seed()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.target.read_bytes(), LAYOUT)
        self.assertEqual(stat.S_IMODE(self.seat_config.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)

    def test_an_existing_seat_config_directory_keeps_the_mode_gdm_gave_it(self):
        self.seat_config.mkdir(parents=True)
        self.seat_config.chmod(0o700)
        code, _out, err = self.seed()
        self.assertEqual(code, 0, err)
        self.assertEqual(stat.S_IMODE(self.seat_config.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)

    def test_a_seat_config_that_is_a_link_is_refused(self):
        (self.gdm / "seat0").mkdir(parents=True)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.seat_config.symlink_to(elsewhere)
        code, _out, err = self.seed()
        self.assertEqual(code, 1)
        self.assertIn("is not a directory — refusing", err)
        self.assertEqual(list(elsewhere.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
