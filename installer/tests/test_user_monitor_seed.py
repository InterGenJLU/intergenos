# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for the created user's install-time monitor-layout seed.

The greeter seed closes the multi-head first-render race for GDM; this seed
closes the SAME race for the user's first login: with no stored
~/.config/monitors.xml, mutter settles the connected-monitor set while the
session's first background paint runs against unsettled state (measured on a
triple-GPU install: solid-color desktop + mis-thrown windows, 233
offscreen-framebuffer failures in the first login's journal, zero on every
boot after a monitors.xml existed). users.seed_user_monitor_layout writes the
synthesized single-primary layout to the target user's ~/.config/monitors.xml
owned by that user (uid/gid from the TARGET's /etc/passwd — the created user
does not exist in the host's), and skips-with-trace rather than failing the
install when the live state is unreadable or the user cannot be resolved: a
root-owned monitors.xml would block the user's own layout updates, so an
unresolvable user writes NOTHING.
"""

import os
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from installer.backend import display, users


def _mode(mode_id, w, h, rate, current=False):
    props = {"is-current": True} if current else {}
    return [mode_id, w, h, rate, 1.0, [1.0, 1.5, 2.0], props]


def _monitor(spec, modes):
    return [list(spec), modes, {}]


def _logical(x, y, scale, primary, specs):
    return [x, y, scale, 0, primary, [list(s) for s in specs], {}]


DUAL_HEAD = [
    1,
    [
        _monitor(("eDP-1", "AUO", "0x1234", "0x0"),
                 [_mode("1920x1080@60", 1920, 1080, 60.0, current=True)]),
        _monitor(("HDMI-1", "DEL", "U2718Q", "4K8X799O0C3L"),
                 [_mode("3840x2160@60", 3840, 2160, 59.997, current=True)]),
    ],
    [
        _logical(0, 0, 1.5, True, [("HDMI-1", "DEL", "U2718Q", "4K8X799O0C3L")]),
        _logical(2560, 0, 1.0, False, [("eDP-1", "AUO", "0x1234", "0x0")]),
    ],
    {},
]


def _write_target_passwd(td, username="tester", uid=1000, gid=1000):
    etc = Path(td) / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "passwd").write_text(
        "root:x:0:0:root:/root:/bin/bash\n"
        f"{username}:x:{uid}:{gid}::/home/{username}:/bin/bash\n")


class TestUserSeedWrite(unittest.TestCase):
    def test_seed_writes_owned_layout_with_home_modes(self):
        with TemporaryDirectory() as td:
            _write_target_passwd(td, "tester", 1234, 5678)
            chowned = []
            with patch.object(display, "read_live_display_state",
                              return_value=DUAL_HEAD), \
                 patch.object(users.trace, "trace_event") as ev, \
                 patch.object(users.os, "chown",
                              side_effect=lambda p, u, g:
                              chowned.append((Path(p), u, g))):
                self.assertTrue(
                    users.seed_user_monitor_layout(td, "tester"))
            seeded = Path(td) / "home/tester/.config/monitors.xml"
            self.assertTrue(seeded.is_file())
            self.assertEqual(os.stat(seeded).st_mode & 0o777, 0o644)
            self.assertEqual(
                os.stat(seeded.parent).st_mode & 0o777, 0o700)
            # The layout is the SAME synthesis the greeter seed uses.
            root = ET.parse(seeded).getroot()
            lm = root.findall("./configuration/logicalmonitor")
            self.assertEqual(len(lm), 1)
            self.assertEqual(lm[0].findtext("primary"), "yes")
            spec = lm[0].find("./monitor/monitorspec")
            self.assertEqual(spec.findtext("connector"), "HDMI-1")
            # Ownership resolved from the TARGET passwd, both paths chowned.
            self.assertIn((seeded, 1234, 5678), chowned)
            self.assertIn((seeded.parent, 1234, 5678), chowned)
            self.assertEqual(ev.call_args.args[0], "user_monitor_seed")
            self.assertEqual(ev.call_args.kwargs["uid"], 1234)

    def test_unreadable_state_skips_traced_never_raises(self):
        with TemporaryDirectory() as td:
            _write_target_passwd(td)
            with patch.object(
                    users.trace, "trace_event") as ev, patch.object(
                    display, "read_live_display_state",
                    side_effect=display.DisplayStateError("no session bus")):
                self.assertFalse(
                    users.seed_user_monitor_layout(td, "tester"))
            self.assertEqual(
                ev.call_args.args[0], "user_monitor_seed_skipped")
            self.assertIn("no session bus", ev.call_args.kwargs["reason"])
            self.assertFalse(
                (Path(td) / "home/tester/.config/monitors.xml").exists())

    def test_unresolvable_user_writes_nothing(self):
        # A root-owned monitors.xml is worse than none — mutter could read
        # it but the user's own layout saves would fail. Skip means SKIP.
        with TemporaryDirectory() as td:
            _write_target_passwd(td, "someoneelse")
            with patch.object(display, "read_live_display_state",
                              return_value=DUAL_HEAD), \
                 patch.object(users.trace, "trace_event") as ev:
                self.assertFalse(
                    users.seed_user_monitor_layout(td, "tester"))
            self.assertEqual(
                ev.call_args.args[0], "user_monitor_seed_skipped")
            self.assertEqual(ev.call_args.kwargs["reason"],
                             "user_not_in_target_passwd")
            self.assertFalse(
                (Path(td) / "home/tester/.config/monitors.xml").exists())

    def test_missing_target_passwd_skips(self):
        with TemporaryDirectory() as td:
            with patch.object(display, "read_live_display_state",
                              return_value=DUAL_HEAD), \
                 patch.object(users.trace, "trace_event") as ev:
                self.assertFalse(
                    users.seed_user_monitor_layout(td, "tester"))
            self.assertEqual(
                ev.call_args.args[0], "user_monitor_seed_skipped")


if __name__ == "__main__":
    unittest.main()
