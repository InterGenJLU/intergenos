#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The driver step does not say reboot after a hook that refused to sign.

WHAT WAS WRONG (a real driver install, 2026-10-16). The driver's post-install
signed its modules, the kernel hook it starts refused to sign the boot image,
and the package manager now records that install as not finished: the row is
marked DEGRADED and the command exits non-zero. The terminal's closing line
already follows the exit status. The page did not: it asked `pkm info`, which
printed the install record and nothing about the marker, read "installed", and
said REBOOT NOW — into a boot image without the driver's command line, on a
machine whose NVIDIA-wired display then stayed dark.

WHAT THIS PROVES. The real `pkm info` (pkm.cli.cmd_info over a real package
database whose driver row carries the marker pkm writes for a refused signing)
prints the marker; the page's own reader takes it from that output; the
outcome for the driver selection says not to reboot yet, names the command
that finishes it and the passphrase it asks for, and is not "installed"; the
driver leg is not done; and a healthy install still ends in the reboot it
needs.
"""

import argparse
import importlib.util
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pkm.cli as pkm_cli
from pkm.database import PackageDB

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "assets" / "intergen-welcome" / "intergen-welcome.py"

_spec = importlib.util.spec_from_file_location("welcome_refused_hook", SCRIPT)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)

REFUSED = "nvidia post-install (refused to sign)"


class _RealPkmInfo:
    """`pkm info` answered by the real command over a fixture database."""

    def __init__(self, degraded):
        self._td = tempfile.TemporaryDirectory()
        root = Path(self._td.name) / "root"
        self.db = PackageDB(Path(self._td.name) / "pkm.db", root=str(root))
        self.db.add_installed("nvidia", "580.159.04", tier="extra")
        if degraded:
            self.db.mark_degraded("nvidia", REFUSED)

    def text(self, name):
        buf = io.StringIO()
        with redirect_stdout(buf):
            pkm_cli.cmd_info(self.db, argparse.Namespace(package=name))
        return buf.getvalue()

    def run(self, argv, **_kwargs):
        """Stands in for subprocess.run, for `pkm info <name>` only."""
        if list(argv[:2]) != ["pkm", "info"]:
            raise AssertionError(f"unexpected command {argv!r}")
        return subprocess.CompletedProcess(argv, 0, stdout=self.text(argv[2]),
                                           stderr="")

    def close(self):
        self.db.close()
        self._td.cleanup()


def _page_asks(test, pkm):
    """Route the page's own `pkm info` call to the real command over ``pkm``."""
    p = mock.patch.object(welcome.subprocess, "run", pkm.run)
    p.start()
    test.addCleanup(p.stop)


def _driver_offer():
    return [{'key': 'driver', 'packages': ['nvidia'], 'activation': 'reboot',
             'proprietary': True}]


class AfterARefusedSigning(unittest.TestCase):
    def setUp(self):
        self.pkm = _RealPkmInfo(degraded=True)
        self.addCleanup(self.pkm.close)
        _page_asks(self, self.pkm)

    def test_pkm_info_carries_the_marker_and_the_page_reads_it(self):
        text = self.pkm.text("nvidia")
        self.assertIn("install_date", text)
        self.assertIn("sudo pkm reinstall nvidia", text)
        self.assertEqual(welcome._unfinished_reason_in(text), REFUSED)
        self.assertIs(welcome._package_is_installed("nvidia"), False)

    def test_the_outcome_does_not_say_reboot(self):
        out = welcome._install_outcome(['driver'], _driver_offer())
        self.assertIsNot(out['installed'], True)
        self.assertEqual([n for n, _ in out['unfinished']], ['nvidia'])
        msg = out['message']
        self.assertNotIn("REBOOT NOW", msg)
        self.assertIn("DO NOT REBOOT for it yet", msg)
        self.assertIn("sudo pkm reinstall nvidia", msg)
        self.assertIn("signing key passphrase", msg)
        self.assertIn("not the MOK enrollment password", msg)

    def test_the_driver_leg_is_not_done(self):
        with mock.patch.object(welcome, "_gpu_detection_record",
                               return_value={'vendor': 'nvidia'}):
            self.assertFalse(welcome._driver_leg_is_done())


class AfterAFinishedInstall(unittest.TestCase):
    """What must not change: a finished install still ends in its reboot."""

    def setUp(self):
        self.pkm = _RealPkmInfo(degraded=False)
        self.addCleanup(self.pkm.close)
        _page_asks(self, self.pkm)

    def test_a_finished_driver_install_still_says_reboot(self):
        self.assertIs(welcome._package_is_installed("nvidia"), True)
        out = welcome._install_outcome(['driver'], _driver_offer())
        self.assertIs(out['installed'], True)
        self.assertIn("REBOOT NOW", out['message'])

    def test_an_injected_probe_never_queries_the_database(self):
        with mock.patch.object(welcome.subprocess, "run",
                               side_effect=AssertionError("queried pkm")):
            out = welcome._install_outcome(['driver'], _driver_offer(),
                                           probe=lambda name: True)
        self.assertIs(out['installed'], True)


if __name__ == "__main__":
    unittest.main()
