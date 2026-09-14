# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The SSH opt-in lands in the removable fragment; the shipped ruleset is never
edited; a serial login prompt appears only when the installer ran over serial.

WHAT WAS WRONG (found on the Intel HP laptop 2026-09-05, reproduced on the
loaner ThinkPad's fresh R001.2-03 install 2026-09-14; R001.3 rows 31 + 13). The
installer inserted `tcp dport 22 accept` into /etc/nftables.conf itself. The
Welcomer's "Enable SSH Server" toggle manages a fragment under /etc/nftables.d
and implements OFF as "delete the fragment" — a file the installer never wrote.
So turning SSH off left the port accepted on every interface, the page said it
was off, and the shipped file contradicted itself three lines below the rule.
The same install enabled a serial login prompt on any machine whose serial
port merely answered an ioctl, whether or not anyone used it.

WHAT THIS PROVES. (1) enable_ssh_server() writes the fragment at the path the
Welcomer deletes, with the bytes the Welcomer writes, and leaves the shipped
/etc/nftables.conf byte-identical — against the REAL shipped file and against
the real file captured from the loaner's R001.2 install (the fixture), which
carries the legacy inline rule and must not gain a second one. (2) The serial
prompt is enabled only when the installer's own console is serial; a working
but unused port no longer earns one. (3) The two copies of the fragment text,
installer and helper, are one shape (byte-equal) — tests/welcome holds the
helper side; here the installer's constant is checked against the helper file
directly so a drift in either place fails in both suites.
"""

import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.backend import users

REPO = Path(__file__).resolve().parent.parent.parent
SHIPPED_CONF = REPO / "packages/core/intergenos-firewall-defaults/nftables.conf"
LOANER_CONF = Path(__file__).parent / "fixtures/nftables.conf.r0012-loaner-install"
HELPER = REPO / "assets/intergen-welcome/intergen-welcome-privhelper"


def _helper_fragment_text():
    """The bytes between the helper's `cat > "$SSH_DROPIN" <<'NFT'` and `NFT`."""
    src = HELPER.read_text()
    m = re.search(r"cat > \"\$SSH_DROPIN\" <<'NFT'\n(.*?)\nNFT\n", src, re.S)
    assert m, "the helper's SSH fragment heredoc was not found"
    return m.group(1) + "\n"


class FragmentIsTheOneShape(unittest.TestCase):
    def test_installer_constant_equals_helper_heredoc(self):
        self.assertEqual(users.SSH_FIREWALL_FRAGMENT, _helper_fragment_text())

    def test_fragment_path_is_the_one_the_helper_deletes(self):
        src = HELPER.read_text()
        self.assertIn('SSH_DROPIN="$NFTD/40-intergen-ssh.conf"', src)
        self.assertIn('NFTD=/etc/nftables.d', src)
        self.assertEqual(users.SSH_FIREWALL_FRAGMENT_RELPATH,
                         "etc/nftables.d/40-intergen-ssh.conf")


class EnableSshServerWritesTheFragment(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.target = self.tmp / "target"
        (self.target / "etc").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _run(self, base_conf_bytes):
        conf = self.target / "etc/nftables.conf"
        conf.write_bytes(base_conf_bytes)
        events = []
        with patch.object(users.trace, "traced_run") as run, \
             patch.object(users.trace, "trace_event",
                          side_effect=lambda *a, **k: events.append((a, k))):
            run.return_value.returncode = 0
            users.enable_ssh_server(self.target)
        return conf, events

    def test_shipped_file_stays_byte_identical_and_fragment_lands(self):
        shipped = SHIPPED_CONF.read_bytes()
        self.assertNotIn(b"dport 22", shipped, "the shipped ruleset must never carry the rule")
        conf, events = self._run(shipped)
        self.assertEqual(conf.read_bytes(), shipped)
        frag = self.target / users.SSH_FIREWALL_FRAGMENT_RELPATH
        self.assertEqual(frag.read_text(), users.SSH_FIREWALL_FRAGMENT)
        self.assertEqual(oct(frag.stat().st_mode & 0o777), "0o644")
        names = [a[0] for a, k in events]
        self.assertIn("ssh_firewall_fragment", names)
        rec = [k for a, k in events if a[0] == "ssh_firewall_fragment"][0]
        self.assertEqual(rec["state"], "written")
        self.assertEqual(rec["size"], len(users.SSH_FIREWALL_FRAGMENT))

    def test_real_r0012_install_file_is_not_edited_again(self):
        """The loaner's real file (legacy inline rule present) gains nothing."""
        legacy = LOANER_CONF.read_bytes()
        self.assertEqual(legacy.count(b"tcp dport 22 accept"), 1)
        conf, _ = self._run(legacy)
        self.assertEqual(conf.read_bytes(), legacy)
        self.assertTrue((self.target / users.SSH_FIREWALL_FRAGMENT_RELPATH).exists())

    def test_second_call_is_idempotent(self):
        conf, _ = self._run(SHIPPED_CONF.read_bytes())
        frag = self.target / users.SSH_FIREWALL_FRAGMENT_RELPATH
        before = frag.stat().st_mtime_ns
        _, events = self._run(SHIPPED_CONF.read_bytes())
        rec = [k for a, k in events if a[0] == "ssh_firewall_fragment"][0]
        self.assertEqual(rec["state"], "present")
        self.assertEqual(frag.read_text(), users.SSH_FIREWALL_FRAGMENT)

    def test_missing_nftables_dir_is_created(self):
        shutil.rmtree(self.target / "etc")
        (self.target / "etc").mkdir()
        conf, _ = self._run(SHIPPED_CONF.read_bytes())
        self.assertTrue((self.target / "etc/nftables.d").is_dir())


class SerialConsoleDecision(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cmdline = self.tmp / "cmdline"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _is_serial(self, cmdline, ttynames):
        self.cmdline.write_text(cmdline)
        def fake_ttyname(fd):
            name = ttynames.get(fd)
            if name is None:
                raise OSError(25, "not a tty")
            return name
        with patch.object(users.os, "ttyname", side_effect=fake_ttyname):
            return users.installer_console_is_serial(str(self.cmdline), fds=(0, 1, 2))

    def test_plain_boot_with_no_serial_stream_is_not_serial(self):
        self.assertFalse(self._is_serial(
            "BOOT_IMAGE=/vmlinuz quiet igos.installer=gui\n",
            {0: "/dev/tty1", 1: "/dev/tty1", 2: "/dev/tty1"}))

    def test_console_ttyS_on_cmdline_is_serial(self):
        self.assertTrue(self._is_serial(
            "BOOT_IMAGE=/vmlinuz console=ttyS0,115200n8 igos.installer=tui\n", {}))

    def test_serial_standard_stream_is_serial(self):
        self.assertTrue(self._is_serial(
            "BOOT_IMAGE=/vmlinuz quiet\n", {0: "/dev/ttyS1"}))

    def test_unreadable_cmdline_and_no_ttys_is_not_serial(self):
        self.assertFalse(users.installer_console_is_serial(
            str(self.tmp / "absent"), fds=()))

    def test_ttyS_substring_elsewhere_does_not_count(self):
        self.assertFalse(self._is_serial(
            "BOOT_IMAGE=/vmlinuz igos.note=console=ttyS0 quiet\n", {}))


class SerialGettyOnlyWhenRequested(unittest.TestCase):
    """enable_serial_getty() enables serial-getty@ttyS0 only when the installer
    ran over a serial console AND the port answers TCGETS — never on a
    working-but-unused port."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.target = self.tmp / "target"
        self.target.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _run(self, requested, port_ok):
        events = []
        def fake_open(path, *a, **k):
            if path == "/dev/ttyS0":
                if port_ok:
                    return 99
                raise OSError(2, "absent")
            return os.open(path, *a, **k)
        with patch.object(users, "installer_console_is_serial", return_value=requested), \
             patch.object(users.os, "open", side_effect=fake_open), \
             patch.object(users.os, "close"), \
             patch.object(users.termios, "tcgetattr", return_value=None), \
             patch.object(users.trace, "traced_run") as run, \
             patch.object(users.trace, "trace_event",
                          side_effect=lambda *a, **k: events.append((a, k))):
            run.return_value.returncode = 0
            users.enable_serial_getty(self.target)
        link = self.target / "etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service"
        return link.is_symlink(), events

    def test_working_port_without_a_serial_installer_gets_no_getty(self):
        enabled, events = self._run(requested=False, port_ok=True)
        self.assertFalse(enabled)
        reasons = [k.get("reason") for a, k in events if a[0] == "serial_getty_skipped"]
        self.assertEqual(reasons, ["installer console not serial"])

    def test_serial_installer_with_working_port_gets_a_getty(self):
        enabled, events = self._run(requested=True, port_ok=True)
        self.assertTrue(enabled)
        self.assertIn("serial_getty_symlink", [a[0] for a, k in events])

    def test_serial_installer_with_dead_port_gets_no_getty(self):
        enabled, events = self._run(requested=True, port_ok=False)
        self.assertFalse(enabled)
        reasons = [k.get("reason") for a, k in events if a[0] == "serial_getty_skipped"]
        self.assertEqual(reasons, ["ttyS0 absent or TCGETS-failed"])


if __name__ == "__main__":
    unittest.main()
