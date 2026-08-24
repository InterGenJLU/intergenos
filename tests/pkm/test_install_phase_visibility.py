#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A long install says what it is doing, and its closing advisory is read.

Two observations from real installs of the graphics packages, on both
graphics paths:

  1. AFTER the download finishes, the package manager prints nothing until
     the package is installed. On a multi-gigabyte package that is a minute
     or more of total silence — one user reported nearly diagnosing it as a
     hang. `pkm/progress.py` already states the rule ("a package manager
     that works for a minute while printing nothing is indistinguishable
     from a package manager that has hung"); the install path did not
     follow it.

  2. The REBOOT REQUIRED block printed in the same plain monochrome as the
     surrounding output and was indistinguishable from it. The project's
     model for a message the user must not miss is the coloured disk-unlock
     prompt; the severity colouring already in `pkm/output.py` is the
     mechanism.

These tests pin both, on the real code paths rather than on the ideas.
"""
import io
import os
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from pkm import output
from pkm.services import format_next_steps


class RecordingReporter:
    """A Reporter stand-in that records the phases an install announced."""

    def __init__(self):
        self.phases = []
        self.level = output.NORMAL

    def phase(self, label, detail=""):
        self.phases.append(label)

    def __getattr__(self, _name):
        return lambda *a, **k: None


class InstallPhasesAreAnnouncedTest(unittest.TestCase):
    """The gap between the download line and the completion line is named."""

    def _archive(self, path):
        """A minimal but real package archive."""
        with tarfile.open(path, "w:gz") as tf:
            payload = b"#!/bin/sh\nexit 0\n"
            info = tarfile.TarInfo("usr/bin/phase-fixture")
            info.size = len(payload)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(payload))
            pkginfo = (b"name = phase-fixture\nversion = 1\nrelease = 1\n"
                       b"description = fixture\n")
            info = tarfile.TarInfo(".PKGINFO")
            info.size = len(pkginfo)
            tf.addfile(info, io.BytesIO(pkginfo))

    def test_the_reporter_can_name_a_phase(self):
        """The vocabulary exists before anything can report through it."""
        self.assertTrue(hasattr(output.Reporter, "phase"))

    def test_extract_and_deploy_are_both_announced(self):
        from pkm.database import PackageDB
        from pkm.installer import PackageInstaller

        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "root"
            root.mkdir()
            archive = Path(d) / "phase-fixture-1-1.igos.tar.gz"
            self._archive(archive)
            db = PackageDB(db_path=str(Path(d) / "pkm.db"))
            installer = PackageInstaller(db, root=root)
            reporter = RecordingReporter()
            ok, msg = installer.install(
                "phase-fixture", archive_path=str(archive), reporter=reporter)
            self.assertTrue(ok, msg)
            self.assertIn("Extract", reporter.phases)
            self.assertIn("Deploy", reporter.phases)
            self.assertLess(reporter.phases.index("Extract"),
                            reporter.phases.index("Deploy"),
                            "the phases are reported in the order they run")


class TheRebootAdvisoryIsLoudTest(unittest.TestCase):
    """A reboot requirement the user's eye slides over is not a notice."""

    REBOOT = ("nvidia", {"requirement": "reboot", "services": [],
                         "reason": "kernel module activates at boot"})
    RESTART = ("intergen", {"requirement": "restart",
                            "services": ["intergen.service"],
                            "reason": "running service upgraded"})

    def test_the_reboot_heading_is_coloured_on_a_terminal(self):
        block = format_next_steps([self.REBOOT], color=True)
        self.assertIn("REBOOT REQUIRED", block)
        self.assertIn("\033[", block, "no colour on the reboot heading")

    def test_only_the_reboot_section_is_coloured(self):
        """The colour is the severity signal. Painting the whole block would
        spend it on the parts that are not urgent."""
        block = format_next_steps([self.RESTART], color=True)
        self.assertNotIn("\033[", block)

    def test_the_block_is_plain_when_colour_is_off(self):
        """Captured output, a pipe and NO_COLOR must stay byte-plain — the
        same rule pkm/output.py already follows."""
        block = format_next_steps([self.REBOOT], color=False)
        self.assertNotIn("\033[", block)
        self.assertIn("REBOOT REQUIRED", block)

    def test_colour_is_off_by_default(self):
        self.assertNotIn("\033[", format_next_steps([self.REBOOT]))

    def test_the_colour_decision_follows_the_stream(self):
        """One place decides, and it is the same one the severity prefixes
        already use, so a piped run cannot be coloured by one and not the
        other."""
        self.assertFalse(output._supports_color(io.StringIO()))


if __name__ == "__main__":
    unittest.main()
