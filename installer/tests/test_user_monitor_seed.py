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


TRIPLE_HEAD = [
    1,
    [
        _monitor(("DP-1", "ACR", "S271HL", "SERIALA"),
                 [_mode("1920x1080@60", 1920, 1080, 60.0, current=True)]),
        _monitor(("DP-2", "ACR", "S271HL", "SERIALB"),
                 [_mode("1920x1080@60", 1920, 1080, 60.0, current=True)]),
        _monitor(("HDMI-1", "ACR", "KA270H", "SERIALC"),
                 [_mode("1920x1080@60", 1920, 1080, 60.0, current=True)]),
    ],
    [
        _logical(0, 0, 1.0, True, [("DP-1", "ACR", "S271HL", "SERIALA")]),
        _logical(1920, 0, 1.0, False, [("DP-2", "ACR", "S271HL", "SERIALB")]),
        _logical(3840, 0, 1.0, False, [("HDMI-1", "ACR", "KA270H", "SERIALC")]),
    ],
    {},
]


class TestUserSeedKeepsEveryMonitorOn(unittest.TestCase):
    """The user's seed must not switch the user's other monitors off.

    WHY THIS CLASS EXISTS. The user seed was written to close a first-login
    race, and it closed it by storing the GREETER's layout — one monitor
    enabled, every other connected monitor in <disabled/>. mutter applies a
    stored configuration that matches the connected set, so on a multi-head
    machine the second and third monitors are lit by the kernel through the
    whole boot scroll and then switched OFF the moment the session starts.
    Measured on an installed three-head system on 2026-08-24: the seeded
    configuration in the user's own monitors.xml listed DP-2 and HDMI-1 under
    <disabled>, and the operator had to re-enable them by hand.

    Closing the race does not require disabling anything: a layout that
    ENABLES every connected monitor is just as stored, just as settled, and is
    what the machine's owner actually has plugged in.
    """

    def _seeded_root(self, td, state):
        _write_target_passwd(td, "tester", 1234, 5678)
        with patch.object(display, "read_live_display_state",
                          return_value=state), \
             patch.object(users.trace, "trace_event"), \
             patch.object(users.os, "chown"):
            self.assertTrue(users.seed_user_monitor_layout(td, "tester"))
        return ET.parse(Path(td) / "home/tester/.config/monitors.xml").getroot()

    def test_no_monitor_is_disabled(self):
        with TemporaryDirectory() as td:
            root = self._seeded_root(td, TRIPLE_HEAD)
            self.assertEqual(
                root.findall("./configuration/disabled"), [],
                "the user's seed disables a monitor the machine has plugged "
                "in; that is the boot-scroll-lit/session-dark defect")

    def test_every_connected_monitor_gets_a_logical_monitor(self):
        with TemporaryDirectory() as td:
            root = self._seeded_root(td, TRIPLE_HEAD)
            connectors = sorted(
                lm.findtext("./monitor/monitorspec/connector")
                for lm in root.findall("./configuration/logicalmonitor"))
            self.assertEqual(connectors, ["DP-1", "DP-2", "HDMI-1"])

    def test_exactly_one_primary_and_it_is_the_live_primary(self):
        with TemporaryDirectory() as td:
            root = self._seeded_root(td, TRIPLE_HEAD)
            primaries = [lm for lm in root.findall("./configuration/logicalmonitor")
                         if lm.findtext("primary") == "yes"]
            self.assertEqual(len(primaries), 1)
            self.assertEqual(
                primaries[0].findtext("./monitor/monitorspec/connector"), "DP-1")
            self.assertEqual(primaries[0].findtext("x"), "0")

    def test_the_logical_monitors_tile_without_overlapping(self):
        with TemporaryDirectory() as td:
            root = self._seeded_root(td, TRIPLE_HEAD)
            spans = []
            for lm in root.findall("./configuration/logicalmonitor"):
                x = int(lm.findtext("x"))
                w = int(lm.findtext("./monitor/mode/width"))
                scale = float(lm.findtext("scale"))
                spans.append((x, x + round(w / scale)))
            spans.sort()
            for (_, end), (start, _) in zip(spans, spans[1:]):
                self.assertEqual(
                    end, start,
                    f"logical monitors do not tile: {spans} — mutter refuses a "
                    "configuration whose logical monitors overlap or gap")

    def test_a_single_head_install_still_gets_one_enabled_monitor(self):
        single = [1, [TRIPLE_HEAD[1][0]],
                  [_logical(0, 0, 1.0, True, [("DP-1", "ACR", "S271HL", "SERIALA")])],
                  {}]
        with TemporaryDirectory() as td:
            root = self._seeded_root(td, single)
            self.assertEqual(
                len(root.findall("./configuration/logicalmonitor")), 1)
            self.assertEqual(root.findall("./configuration/disabled"), [])


class TestGreeterSeedStaysPrimaryOnly(unittest.TestCase):
    """Guard against over-correcting: the GREETER seed is a different question.

    The greeter is a login prompt on one screen; rendering it stretched across
    every monitor is the fallback the greeter seed exists to avoid. Its
    single-primary layout is deliberate and stays. This class fails if the fix
    for the user seed is applied to the greeter as well.
    """

    def test_the_greeter_synthesis_still_disables_the_others(self):
        xml = display.synthesize_primary_only_layout(TRIPLE_HEAD)
        root = ET.fromstring(xml)
        self.assertEqual(len(root.findall("./configuration/logicalmonitor")), 1)
        disabled = root.findall("./configuration/disabled/monitorspec")
        self.assertEqual(
            sorted(d.findtext("connector") for d in disabled),
            ["DP-2", "HDMI-1"])


if __name__ == "__main__":
    unittest.main()
