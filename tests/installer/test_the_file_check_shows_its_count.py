#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The installed-file check says what it is doing and shows a count.

After the post-install hooks the installer re-reads every installed file to
record its checksum (packages.reconcile_checksums). That read took 2 min 48 s
in a virtual machine and 20.7 s on real hardware, while the screen still said
"post-install hooks complete" and the bar stood still.

The check is now announced with its file count and that it takes a few
minutes, then reports a count after each batch: the graphical installer keeps
that line on the bar, shows "x of y" under it and moves the bar forward; the
text installer rewrites one counting line in place. These tests pin: the
batched check updates exactly the rows the single call updates, each distinct
path once, and commits; the count starts at 0 and ends at the total; the
orchestrator announces and counts before it calls the phase complete; the
progress screen keeps the line, shows the count and never moves the bar
backwards; the text reporter counts on one line.
"""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from installer.backend import packages  # noqa: E402
from installer.frontend import tui  # noqa: E402
from pkm.database import PackageDB, _sha256  # noqa: E402

DECLARATION = ("Checking all 945,386 installed files "
               "(this process will take a few minutes)")


class TheBatchedCheckMatchesTheSingleCall(unittest.TestCase):
    """4,500 files; 500 of them owned by two packages (a base and its -pass2)."""

    N = 4500

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._td.cleanup()

    def _target(self, name):
        target = Path(self._td.name) / name
        (target / "var" / "lib" / "igos").mkdir(parents=True)
        rels = ["usr/share/check/f%05d" % i for i in range(self.N)]
        for rel in rels:
            p = target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(rel.encode())
        db = PackageDB(str(target / "var/lib/igos/pkm.db"), root=str(target))
        try:
            for pkg, own in (("base", rels[:3000]), ("base-pass2", rels[2500:])):
                pkg_id = db.add_installed(name=pkg, version="1.0", release=1,
                                          tier="core", install_method="archive")
                db.add_files(pkg_id, own, hashes={r: "0" * 64 for r in own})
        finally:
            db.close()
        return target

    def _rows(self, target):
        db = PackageDB(str(target / "var/lib/igos/pkm.db"), root=str(target))
        try:
            return sorted(db.conn.execute(
                "SELECT path, checksum FROM files").fetchall())
        finally:
            db.close()

    def test_the_rows_and_the_count(self):
        single = self._target("single")
        batched = self._target("batched")
        names = ["base", "base-pass2"]
        packages.reconcile_checksums(single, installed_names=names)
        seen = []
        packages.reconcile_checksums(
            batched, installed_names=names,
            progress=lambda done, total: seen.append((done, total)), batch=1000)
        self.assertEqual(seen, [(0, 4500), (1000, 4500), (2000, 4500),
                                (3000, 4500), (4000, 4500), (4500, 4500)])
        rows = self._rows(batched)
        self.assertEqual(rows, self._rows(single))
        self.assertEqual(len(rows), 5000)  # the 500 shared paths have two rows
        for path, checksum in rows:
            self.assertEqual(checksum, _sha256(str(batched / path)), path)

    def test_the_default_batch_reports_about_one_percent(self):
        target = self._target("default")
        seen = []
        packages.reconcile_checksums(
            target, installed_names=["base", "base-pass2"],
            progress=lambda done, total: seen.append(done))
        self.assertEqual(seen, [0, 2000, 4000, 4500])  # at least 2,000 a batch


class TheOrchestratorCountsBeforeItCallsThePhaseComplete(unittest.TestCase):
    def test_the_order(self):
        src = (REPO_ROOT / "installer/backend/install.py").read_text()
        announce = src.index('f"Checking all {total_files:,} installed files "')
        check = src.index("progress=_file_check_progress")
        complete = src.index(
            '_emit(PHASE_HOOKS, 11, "post-install hooks complete; root password kept")')
        self.assertLess(announce, check)
        self.assertLess(check, complete)
        self.assertIn('f"(this process will take a few minutes)"', src)
        self.assertIn("progress_callback(PHASE_HOOKS, done, total_files, \"\")", src)


class _Bar:
    def __init__(self):
        self.fractions = []
        self.text = None

    def set_fraction(self, fraction):
        self.fractions.append(fraction)

    def set_text(self, text):
        self.text = text


class _Label:
    text = None

    def set_label(self, text):
        self.text = text


class TheProgressScreenKeepsTheLineAndCounts(unittest.TestCase):
    def setUp(self):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Adw", "1")
            from installer.frontend.gui.screens import progress
        except (ImportError, ValueError) as exc:
            self.skipTest("GTK 4 is not importable here: %s" % exc)
        # The page is a GTK widget; its two progress methods are driven on a
        # plain stand-in carrying the same attributes.
        cls = progress.ProgressPage
        page = SimpleNamespace(
            _progress_bar=_Bar(), _status_label=_Label(), _phases_total=13,
            _last_boundary_fraction=0.0, _shown_fraction=0.0,
            _phase_line=None, _count_base=None)
        page._set_fraction = lambda f: cls._set_fraction(page, f)
        page._on_progress_event = (
            lambda *a: cls._on_progress_event(page, *a))
        self.page = page

    def test_the_sequence(self):
        p = self.page
        p._on_progress_event("hooks", 10, 13, "running post-install hooks")
        p._on_progress_event("hooks", 115, 115, "xdg-desktop-portal-gnome")
        p._on_progress_event("hooks", 10, 13, DECLARATION)
        line = "Phase 10/13: hooks — " + DECLARATION
        self.assertEqual(p._status_label.text, line)
        p._on_progress_event("hooks", 0, 945386, "")
        before = p._progress_bar.fractions[-1]
        p._on_progress_event("hooks", 472693, 945386, "")
        self.assertEqual(p._progress_bar.text, line)
        self.assertEqual(p._status_label.text, "472,693 of 945,386")
        middle = p._progress_bar.fractions[-1]
        p._on_progress_event("hooks", 945386, 945386, "")
        end = p._progress_bar.fractions[-1]
        self.assertLess(before, middle)
        self.assertLess(middle, end)
        p._on_progress_event("hooks", 11, 13, "post-install hooks complete; root password kept")
        p._on_progress_event("services", 11, 13, "enabling services")
        fr = p._progress_bar.fractions
        self.assertEqual(fr, sorted(fr), "the bar moved backwards")
        self.assertEqual(p._status_label.text, "Phase 11/13: services — enabling services")

    def test_a_package_update_is_unchanged(self):
        p = self.page
        p._on_progress_event("packages", 5, 13, "installing 4 group(s)")
        p._on_progress_event("packages", 598, 787, "mesa")
        self.assertEqual(p._progress_bar.text, "packages: 598/787")
        self.assertEqual(p._status_label.text, "packages: mesa (598/787)")


class _Terminal(io.StringIO):
    def isatty(self):
        return True


class TheTextInstallerCountsOnOneLine(unittest.TestCase):
    def test_a_terminal_gets_one_line_rewritten_in_place(self):
        out = _Terminal()
        reporter = tui._ForgeReporter(out=out)
        for done in (0, 472693, 945386):
            reporter.count(done, 945386)
        text = out.getvalue()
        self.assertEqual(text.count("\r"), 3)
        self.assertEqual(text.count("\n"), 1)
        self.assertIn("472,693 of 945,386", text)
        self.assertTrue(text.endswith("945,386 of 945,386\n"))

    def test_a_redirected_stream_gets_only_the_final_count(self):
        out = io.StringIO()
        reporter = tui._ForgeReporter(out=out)
        for done in (0, 5, 10):
            reporter.count(done, 10)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("10 of 10"))

    def test_the_count_branch_comes_first(self):
        src = (REPO_ROOT / "installer/frontend/tui.py").read_text()
        self.assertLess(src.index("_reporter.count(current, total)"),
                        src.index("if phase == backend_install.PHASE_PACKAGES and total != phases_total:"))


if __name__ == "__main__":
    unittest.main()
