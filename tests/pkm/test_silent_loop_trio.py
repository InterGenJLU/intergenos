#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The three measured silent loops: verify --all, import, and remove.

WHY THIS FILE EXISTS. Three pkm operations were measured working for a long
time while printing nothing between the command and its result:

  S1  `pkm verify --all` — a strict whole-system verify reads and hashes every
      owned file on the machine. Measured at over forty seconds of silence.
  S2  `pkm import` — walks every installed package's text manifest and rewrites
      the file rows of each one that changed. One line before, one after.
  S3  `pkm remove` — unlinks a package's whole payload, then walks the ancestor
      closure of every path it touched. A large package is a hundred thousand
      unlinks with nothing said until the end.

An operation that prints nothing cannot be told from one that has hung, and and the
decision recorded 2026-08-06 is that this is not acceptable in a package
manager: a user who cannot tell work from a freeze reasonably concludes the
system is broken.

WHAT THESE CASES ASSERT, and why they are BEHAVIOURAL rather than structural:
they drive the real command handlers and read what a user would actually see.
That is what makes them red on a tree without the change for the right reason —
the missing OUTPUT — rather than red because a function signature does not exist
yet, which would prove only that the code is new.

The -q cases are the load-bearing half of the standard. A request for quiet must
not be able to produce a silent multi-minute pause, so the opening and closing
lines still print at -q while everything between them is suppressed.
"""

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pkm.cli as cli
from pkm import output
from pkm.database import PackageDB
from pkm.remover import PackageRemover


class _CaptureLevel:
    """Run a block at a given pkm verbosity, capturing everything printed."""

    def __init__(self, level):
        self.level = level
        self.buf = io.StringIO()

    def __enter__(self):
        self._prior = output.process_level()
        output.set_process_level(self.level)
        output._process_reporter.stream = self.buf
        self._redirect = redirect_stdout(self.buf)
        self._redirect.__enter__()
        return self

    def __exit__(self, *exc):
        self._redirect.__exit__(*exc)
        output.set_process_level(self._prior)
        output._process_reporter.stream = None
        return False

    @property
    def text(self):
        return self.buf.getvalue()


class SilentLoopTestBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def add_package(self, name, version="1.0", files=("usr/bin/x",)):
        pid = self.db.add_installed(name, version, tier="core")
        for rel in files:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"{name}\n")
        self.db.add_files(pid, list(files))
        return pid


class S1VerifyAllTest(SilentLoopTestBase):
    """`pkm verify --all` announces, reports its part, and states the outcome."""

    def _run_verify_all(self, level):
        for i in range(3):
            self.add_package(f"pkg{i}", files=(f"usr/bin/pkg{i}",))
        args = argparse.Namespace(
            verify_all=True, package=None, verify_mode="fast",
            verify_detail=False,
        )
        cap = _CaptureLevel(level)
        with cap:
            try:
                cli.cmd_verify(self.db, args)
            except SystemExit:
                pass
        return cap.text

    def test_announces_before_it_starts(self):
        text = self._run_verify_all(output.NORMAL)
        self.assertIn("Verifying installed packages", text)

    def test_states_how_much_work_there_is(self):
        """The scale is what lets a user judge whether the wait is reasonable."""
        text = self._run_verify_all(output.NORMAL)
        self.assertIn("3 packages to check", text)

    def test_names_the_part_that_is_running(self):
        text = self._run_verify_all(output.NORMAL)
        self.assertIn("comparing them against disk", text)

    def test_reports_the_outcome(self):
        text = self._run_verify_all(output.NORMAL)
        self.assertIn("3 ok", text)

    def test_the_brackets_survive_quiet(self):
        text = self._run_verify_all(output.QUIET)
        self.assertIn("Verifying installed packages", text)
        self.assertIn("3 ok", text)

    def test_quiet_still_suppresses_the_detail_between_them(self):
        text = self._run_verify_all(output.QUIET)
        self.assertNotIn("packages to check", text)
        self.assertNotIn("comparing them against disk", text)

    def test_the_per_package_callback_actually_fires(self):
        """A heartbeat that is never driven cannot report anything. This proves
        the seam is called once per package, in order, with the running index —
        rather than assuming a callback parameter means it is used."""
        for i in range(4):
            self.add_package(f"p{i}", files=(f"usr/bin/p{i}",))
        seen = []
        from pkm.verifier import PackageVerifier
        PackageVerifier(self.db).verify_all(
            mode="fast", on_package=lambda i, t, n: seen.append((i, t, n)))
        self.assertEqual([i for i, _, _ in seen], [1, 2, 3, 4])
        self.assertEqual({t for _, t, _ in seen}, {4})
        self.assertEqual(sorted(n for _, _, n in seen),
                         ["p0", "p1", "p2", "p3"])

    def test_a_raising_callback_cannot_abort_the_verification(self):
        """Reporting must never be able to stop the work it describes."""
        for i in range(3):
            self.add_package(f"q{i}", files=(f"usr/bin/q{i}",))
        from pkm.verifier import PackageVerifier

        def explode(*_a):
            raise RuntimeError("reporter is broken")

        results = PackageVerifier(self.db).verify_all(
            mode="fast", on_package=explode)
        self.assertEqual(len(results), 3)


class S2ImportTest(SilentLoopTestBase):
    """`pkm import` announces, reports its part, and states the outcome."""

    def _write_manifests(self, count):
        mdir = self.tmp / "manifests"
        mdir.mkdir()
        for i in range(count):
            (mdir / f"m{i}-1.0").write_text(
                f"PACKAGE NAME: m{i}-1.0\n"
                f"PACKAGE VERSION: 1.0\n"
                f"BUILD DATE: 2026-01-01T00:00:00Z\n"
                f"FILE LIST:\n"
                f"usr/share/m{i}\n"
            )
        return mdir

    def _run_import(self, level, mdir):
        args = argparse.Namespace()
        cap = _CaptureLevel(level)
        real = self.db.import_manifests

        def scoped(manifest_dir=None, on_manifest=None):
            # The progress callback is forwarded only when the underlying
            # method accepts one. That is deliberate: it keeps the OUTPUT
            # cases in this class testing the OUTPUT. Against a tree whose
            # import has no progress seam they still run and fail on the
            # missing announce and outcome lines — a red that describes the
            # defect — instead of dying on an unexpected keyword, which would
            # only report that the signature is new.
            import inspect
            if "on_manifest" in inspect.signature(real).parameters:
                return real(manifest_dir=mdir, on_manifest=on_manifest)
            return real(manifest_dir=mdir)

        with cap, patch.object(self.db, "import_manifests", scoped):
            cli.cmd_import(self.db, args)
        return cap.text

    def test_announces_before_it_starts(self):
        text = self._run_import(output.NORMAL, self._write_manifests(3))
        self.assertIn("Importing installed package manifests", text)

    def test_names_the_part_that_is_running(self):
        text = self._run_import(output.NORMAL, self._write_manifests(3))
        self.assertIn("reading manifests", text)

    def test_reports_the_outcome(self):
        text = self._run_import(output.NORMAL, self._write_manifests(3))
        self.assertIn("3 package(s) imported", text)

    def test_the_brackets_survive_quiet(self):
        text = self._run_import(output.QUIET, self._write_manifests(2))
        self.assertIn("Importing installed package manifests", text)
        self.assertIn("2 package(s) imported", text)

    def test_the_per_manifest_callback_actually_fires(self):
        mdir = self._write_manifests(3)
        seen = []
        self.db.import_manifests(
            manifest_dir=mdir,
            on_manifest=lambda i, t, n: seen.append((i, t, n)))
        self.assertEqual([i for i, _, _ in seen], [1, 2, 3])
        self.assertEqual({t for _, t, _ in seen}, {3})

    def test_a_raising_callback_cannot_abort_the_import(self):
        mdir = self._write_manifests(3)

        def explode(*_a):
            raise RuntimeError("reporter is broken")

        count = self.db.import_manifests(manifest_dir=mdir,
                                         on_manifest=explode)
        self.assertEqual(count, 3)


class S3RemoveTest(SilentLoopTestBase):
    """`pkm remove` announces, reports its part, and states the outcome."""

    def _run_remove(self, level, name):
        args = argparse.Namespace(package=name, force=True, quiet=False,
                                  verbose=False)
        cap = _CaptureLevel(level)
        with cap, \
             patch.object(cli, "refresh_available_updates_after_transaction",
                          lambda *_a, **_k: None), \
             patch.object(cli, "PackageRemover",
                          lambda db: PackageRemover(db, root=self.root)):
            try:
                cli.cmd_remove(self.db, args)
            except SystemExit:
                pass
        return cap.text

    def test_announces_before_it_starts(self):
        self.add_package("victim", files=("usr/bin/victim",))
        text = self._run_remove(output.NORMAL, "victim")
        self.assertIn("Removing victim", text)

    def test_names_the_part_that_is_running(self):
        self.add_package("victim", files=("usr/bin/victim",))
        text = self._run_remove(output.NORMAL, "victim")
        self.assertIn("unlinking recorded files", text)

    def test_the_brackets_survive_quiet(self):
        self.add_package("victim", files=("usr/bin/victim",))
        text = self._run_remove(output.QUIET, "victim")
        self.assertIn("Removing victim", text)

    def test_the_per_file_callback_fires_for_every_considered_path(self):
        """Deliberately counted over every CONSIDERED path, not only the
        unlinked ones: a removal that spends its time skipping co-owned paths
        is working just as hard, and reporting only the deletions would make
        that case look like a hang again."""
        files = tuple(f"usr/share/victim/f{i}" for i in range(5))
        self.add_package("victim", files=files)
        seen = []
        PackageRemover(self.db, root=self.root).remove(
            "victim", force=True,
            on_file=lambda i, t, p: seen.append((i, t, p)))
        self.assertGreaterEqual(len(seen), len(files))
        # The running index never exceeds the declared total — a count that
        # overshoots reads as broken and is worse than no count.
        for i, t, _ in seen:
            self.assertLessEqual(i, t)

    def test_a_raising_callback_cannot_abort_the_removal(self):
        self.add_package("victim", files=("usr/bin/victim",))

        def explode(*_a):
            raise RuntimeError("reporter is broken")

        ok, _msg = PackageRemover(self.db, root=self.root).remove(
            "victim", force=True, on_file=explode)
        self.assertTrue(ok)
        self.assertIsNone(self.db.get_installed("victim"))


if __name__ == "__main__":
    unittest.main()
