# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The done-marker must also stop the OTHER launcher, not just the systemd one.

WHAT THIS FIXES, measured on an installed machine rather than assumed. The
package already ships a systemd drop-in carrying
``ConditionPathExists=!%h/.config/intergen-welcome/done`` on the unit that
systemd-xdg-autostart-generator produces, and that drop-in WORKS: on a machine
whose marker is present, ``systemctl --user show
'app-intergen\\x2dwelcome@autostart.service' -p ConditionResult`` reports
``ConditionResult=no`` and no process is started.

The error it was written for kept happening anyway, because the process that
launches the Welcomer at login is not that unit. The journal of a login on an
already-set-up machine shows::

    gnome-session-service[3352]: Could not create transient scope for PID 3850:
        GDBus.Error:org.freedesktop.DBus.Error.UnixProcessIdUnknown:
        Failed to set unit properties: No such process
    systemd[2902]: app-gnome-intergen\\x2dwelcome-3860.scope: No PIDs left to
        attach to the scope's control group, refusing.
    systemd[2902]: app-gnome-intergen\\x2dwelcome-3860.scope: Failed with
        result 'resources'.
    systemd[2902]: Failed to start Application launched by gnome-session-service.

``gnome-session-service`` reads /etc/xdg/autostart directly and honours no
systemd unit condition, so the drop-in cannot reach it. The scope it creates is
named ``app-gnome-<entry>-<pid>.scope``, which is how the two launchers are told
apart in a journal.

``AutostartCondition=`` cannot gate it either: that key is delegated to
``gnome-systemd-autostart-condition``, which this system does not ship.

THE REMEDY, and why this one. The wrapper writes a per-user XDG autostart
override — ``~/.config/autostart/intergen-welcome.desktop`` carrying
``Hidden=true`` — alongside the done-marker. ``Hidden=true`` is the
Desktop-Entry-spec instruction to treat the entry as though it did not exist,
and BOTH launchers honour it before spawning anything, so the fix is a
gate BEFORE a process exists rather than a process made to live longer. Making
the process live longer was rejected: it would hide the race instead of removing
it, and a sleep in a login path is a cost every user pays forever.

The override is written on the same three occasions the marker is: when a clean
run completes, when an already-done run exits early (which is what repairs a
machine that was set up before this change), and it is REMOVED when the re-arm
sentinel clears the marker, so the promise that the Welcomer returns after a
driver reboot still holds.
"""

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUILD_SH = REPO / "packages" / "desktop" / "intergen-welcome" / "build.sh"
OVERRIDE_REL = ".config/autostart/intergen-welcome.desktop"
MARKER_REL = ".config/intergen-welcome/done"
REARM_REL = ".config/intergen-welcome/rearm"


def extract_wrapper() -> str:
    """The wrapper script exactly as build.sh writes it to /usr/bin."""
    text = BUILD_SH.read_text(encoding="utf-8")
    m = re.search(r"<<'WRAPPER'\n(.*?)\nWRAPPER\n", text, re.DOTALL)
    if not m:
        raise AssertionError(
            "the WRAPPER heredoc is no longer in build.sh in the shape this "
            "test extracts; update the test with the recipe")
    return m.group(1)


class WrapperHarness:
    """Run the real wrapper against a temporary HOME with a stubbed app."""

    def __init__(self, tmp: Path, app_rc: int = 0):
        self.home = tmp / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        bindir = tmp / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        self.wrapper = bindir / "intergen-welcome"
        self.wrapper.write_text(extract_wrapper(), encoding="utf-8")
        self.wrapper.chmod(self.wrapper.stat().st_mode | stat.S_IXUSR)
        # A python3 that stands in for the Welcomer itself: it must not be the
        # real GTK application, and its exit code is what the wrapper's
        # bookkeeping keys on.
        stub = bindir / "python3"
        stub.write_text("#!/bin/sh\nexit %d\n" % app_rc, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        self.bindir = bindir

    def run(self, *args):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PATH"] = "%s:%s" % (self.bindir, env.get("PATH", ""))
        return subprocess.run([str(self.wrapper), *args], env=env,
                              capture_output=True, text=True, timeout=60)

    def path(self, rel):
        return self.home / rel


class AutostartOverrideTest(unittest.TestCase):

    def test_clean_run_writes_the_override_beside_the_marker(self):
        with tempfile.TemporaryDirectory() as td:
            h = WrapperHarness(Path(td), app_rc=0)
            r = h.run()
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(h.path(MARKER_REL).exists(),
                            "the done-marker is the existing behaviour and must stay")
            override = h.path(OVERRIDE_REL)
            self.assertTrue(
                override.exists(),
                "a clean run wrote the done-marker but no per-user autostart "
                "override, so gnome-session-service still launches the Welcomer "
                "at the next login and still fails to scope it")
            body = override.read_text(encoding="utf-8")
            self.assertIn("Hidden=true", body,
                          "the override exists but does not carry Hidden=true, "
                          "which is the only line that stops the launch")
            self.assertIn("[Desktop Entry]", body)

    def test_already_done_early_exit_repairs_a_missing_override(self):
        """A machine set up BEFORE this change has the marker and no override.

        The early-exit path is the only code that runs on such a machine, so it
        is the path that has to repair it — otherwise the fix never reaches an
        existing install.
        """
        with tempfile.TemporaryDirectory() as td:
            h = WrapperHarness(Path(td), app_rc=0)
            h.path(MARKER_REL).parent.mkdir(parents=True, exist_ok=True)
            h.path(MARKER_REL).touch()
            r = h.run()
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(
                h.path(OVERRIDE_REL).exists(),
                "the wrapper exited early on the marker and left no override, "
                "so an already-set-up machine keeps producing the scope error "
                "at every login")

    def test_rearm_removes_the_override_with_the_marker(self):
        """The driver-install path promises the Welcomer comes back.

        It clears the done-marker; if the override survived, the entry would
        stay hidden and the promise would be false.
        """
        with tempfile.TemporaryDirectory() as td:
            h = WrapperHarness(Path(td), app_rc=0)
            h.run()                                   # first clean run
            self.assertTrue(h.path(OVERRIDE_REL).exists())
            h.path(REARM_REL).parent.mkdir(parents=True, exist_ok=True)
            h.path(REARM_REL).touch()                 # a driver install started
            h.run()
            self.assertFalse(h.path(MARKER_REL).exists(),
                             "re-arm must clear the marker (existing behaviour)")
            self.assertFalse(
                h.path(OVERRIDE_REL).exists(),
                "re-arm cleared the marker but left the Hidden override, so the "
                "Welcomer would never come back after the driver reboot")

    def test_a_failed_run_writes_neither(self):
        with tempfile.TemporaryDirectory() as td:
            h = WrapperHarness(Path(td), app_rc=3)
            r = h.run()
            self.assertEqual(r.returncode, 3)
            self.assertFalse(h.path(MARKER_REL).exists())
            self.assertFalse(
                h.path(OVERRIDE_REL).exists(),
                "a run that did not complete must not hide the autostart entry")


if __name__ == "__main__":
    unittest.main()
