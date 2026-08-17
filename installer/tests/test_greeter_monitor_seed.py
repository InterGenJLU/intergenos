# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for the pre-configuration greeter monitor seed.

display.synthesize_primary_only_layout renders a monitors.xml enabling
exactly the live session's primary logical monitor (current mode, logical
scale) with every other connected monitor explicitly <disabled/> — mutter
matches stored configurations against the FULL connected set, so the
disabled list is load-bearing. users.seed_greeter_monitor_layout writes the
layout to the seat state AND the staged-seed path with the F31 mode-bit
doctrine (dirs 0755, files 0644), and SKIPS with a traced reason instead of
failing the install when the live session's state is unreadable.
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

SINGLE_HEAD = [
    1,
    [_monitor(("eDP-1", "AUO", "0x1234", "0x0"),
              [_mode("1920x1080@60", 1920, 1080, 60.0, current=True)])],
    [_logical(0, 0, 1.0, True, [("eDP-1", "AUO", "0x1234", "0x0")])],
    {},
]


class TestSynthesis(unittest.TestCase):
    def test_dual_head_enables_primary_disables_rest(self):
        xml = display.synthesize_primary_only_layout(DUAL_HEAD)
        root = ET.fromstring(xml)
        self.assertEqual(root.tag, "monitors")
        self.assertEqual(root.get("version"), "2")
        logical = root.findall("./configuration/logicalmonitor")
        self.assertEqual(len(logical), 1)
        lm = logical[0]
        self.assertEqual(lm.findtext("primary"), "yes")
        self.assertEqual(lm.findtext("x"), "0")
        self.assertEqual(lm.findtext("y"), "0")
        self.assertEqual(lm.findtext("scale"), "1.5")
        spec = lm.find("./monitor/monitorspec")
        self.assertEqual(spec.findtext("connector"), "HDMI-1")
        self.assertEqual(spec.findtext("serial"), "4K8X799O0C3L")
        mode = lm.find("./monitor/mode")
        self.assertEqual(mode.findtext("width"), "3840")
        self.assertEqual(mode.findtext("height"), "2160")
        self.assertEqual(mode.findtext("rate"), "59.997")
        disabled = root.findall("./configuration/disabled/monitorspec")
        self.assertEqual(len(disabled), 1)
        self.assertEqual(disabled[0].findtext("connector"), "eDP-1")

    def test_single_head_has_no_disabled_block(self):
        xml = display.synthesize_primary_only_layout(SINGLE_HEAD)
        root = ET.fromstring(xml)
        self.assertEqual(
            len(root.findall("./configuration/logicalmonitor")), 1)
        self.assertEqual(root.findall("./configuration/disabled"), [])
        self.assertEqual(
            root.find("./configuration/logicalmonitor").findtext("scale"),
            "1")

    def test_no_primary_flag_falls_back_to_first_logical(self):
        state = [1, DUAL_HEAD[1],
                 [_logical(0, 0, 1.0, False,
                           [("eDP-1", "AUO", "0x1234", "0x0")])],
                 {}]
        xml = display.synthesize_primary_only_layout(state)
        spec = ET.fromstring(xml).find(
            "./configuration/logicalmonitor/monitor/monitorspec")
        self.assertEqual(spec.findtext("connector"), "eDP-1")

    def test_no_monitors_raises(self):
        with self.assertRaises(display.DisplayStateError):
            display.synthesize_primary_only_layout([1, [], [], {}])

    def test_no_current_mode_raises(self):
        state = [1,
                 [_monitor(("eDP-1", "A", "B", "C"),
                           [_mode("m", 1920, 1080, 60.0, current=False)])],
                 [_logical(0, 0, 1.0, True, [("eDP-1", "A", "B", "C")])],
                 {}]
        with self.assertRaises(display.DisplayStateError):
            display.synthesize_primary_only_layout(state)


class TestSeedWrite(unittest.TestCase):
    def test_seed_writes_both_paths_with_doctrine_modes(self):
        with TemporaryDirectory() as td:
            with patch.object(display, "read_live_display_state",
                              return_value=DUAL_HEAD):
                with patch.object(users.trace, "trace_event") as ev:
                    self.assertTrue(users.seed_greeter_monitor_layout(td))
            staged = Path(td) / "var/lib/igos/greeter-monitors-seed.xml"
            seeded = Path(td) / "var/lib/gdm/seat0/config/monitors.xml"
            for f in (staged, seeded):
                self.assertTrue(f.is_file(), f)
                self.assertEqual(os.stat(f).st_mode & 0o777, 0o644, f)
                ET.parse(f)  # well-formed — the seed unit's own gate
            for d in (seeded.parent, seeded.parent.parent):
                self.assertEqual(os.stat(d).st_mode & 0o777, 0o755, d)
            self.assertEqual(staged.read_text(), seeded.read_text())
            self.assertEqual(ev.call_args.args[0], "greeter_monitor_seed")

    def test_unreadable_state_skips_traced_never_raises(self):
        with TemporaryDirectory() as td:
            with patch.object(
                    users.trace, "trace_event") as ev, patch.object(
                    display, "read_live_display_state",
                    side_effect=display.DisplayStateError("no session bus")):
                self.assertFalse(users.seed_greeter_monitor_layout(td))
            self.assertEqual(
                ev.call_args.args[0], "greeter_monitor_seed_skipped")
            self.assertIn("no session bus", ev.call_args.kwargs["reason"])
            self.assertFalse(
                (Path(td) / "var/lib/gdm/seat0/config/monitors.xml").exists())


class TestReader(unittest.TestCase):
    def test_reader_runs_busctl_as_live_user_and_parses(self):
        import json
        from types import SimpleNamespace

        def fake_run(cmd, **kw):
            # setpriv, not runuser: the live image ships setpriv (util-linux)
            # and does NOT ship runuser — the seed skipped on every install
            # until this was measured on an installed ge9b-12 system.
            self.assertEqual(cmd[0], "setpriv")
            self.assertIn("--reuid=1000", cmd)
            self.assertIn("--regid=1000", cmd)
            self.assertIn("--init-groups", cmd)
            self.assertIn("busctl", cmd)
            self.assertIn("GetCurrentState", cmd)
            return SimpleNamespace(
                returncode=0, stderr="",
                stdout=json.dumps({"type": "x", "data": DUAL_HEAD}))
        state = display.read_live_display_state(
            _runner=fake_run,
            _pw_lookup=lambda u: SimpleNamespace(pw_uid=1000, pw_gid=1000))
        self.assertEqual(state, DUAL_HEAD)

    def test_reader_unknown_user_raises_display_error(self):
        # A missing live user must surface as DisplayStateError so the
        # caller's traced-SKIP posture holds (never an unhandled KeyError).
        def no_user(name):
            raise KeyError(name)
        with self.assertRaises(display.DisplayStateError):
            display.read_live_display_state(
                _runner=lambda *a, **k: None, _pw_lookup=no_user)

    def test_reader_nonzero_rc_raises(self):
        from types import SimpleNamespace
        with self.assertRaises(display.DisplayStateError):
            display.read_live_display_state(
                _runner=lambda *a, **k:
                    SimpleNamespace(returncode=1, stdout="", stderr="boom"),
                _pw_lookup=lambda u: SimpleNamespace(pw_uid=1000, pw_gid=1000))


if __name__ == "__main__":
    unittest.main()
