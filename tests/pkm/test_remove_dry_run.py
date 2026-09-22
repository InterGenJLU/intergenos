# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`pkm remove --dry-run` shows the plan it already computes and unlinks nothing.

WHY THIS EXISTS. Every other destructive pkm verb can be previewed: `upgrade`,
`autoremove` and `iso-prep` all take --dry-run, and `upgrade` refuses outright
on a non-tty without --yes. `remove` had neither. The only way to learn what a
removal would take off a machine was to remove it, and a removal is exactly the
operation whose consequences a person most wants to see first — it is the verb
that can unlink a file the package cannot put back.

The plan is not new work. `remove()` already classifies every recorded path
before it unlinks anything: what it will unlink, what it retains because
another installed package co-owns it, what it retains because the package's own
hook created it here, what it refuses as system skeleton, and which /etc files
it preserves because the person edited them. --dry-run prints that
classification and stops.

WHAT IS PINNED HERE: nothing on disk changes, the database row survives, the
hooks do not fire, and the report names the paths in each class. The last two
cases are the non-masking controls — a real removal still removes.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB
from pkm.remover import PackageRemover
from pkm import cli


class RemovalFixture(unittest.TestCase):
    """One package with payload, a co-owned path and a hook-created row."""

    PAYLOAD = "usr/libexec/example/helper"
    SECOND_PAYLOAD = "usr/share/example/data.txt"
    CO_OWNED = "usr/share/shared/common.conf"
    HOOK_MADE = "etc/example/made-here.conf"
    EDITED_CONFIG = "etc/example/edited.conf"

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pkm-remove-dry-run-")
        self.root = Path(self._tmp) / "root"
        self.db_path = Path(self._tmp) / "pkm.db"
        for rel in (self.PAYLOAD, self.SECOND_PAYLOAD, self.CO_OWNED,
                    self.HOOK_MADE, self.EDITED_CONFIG):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"contents of {rel}\n")
        self.db = PackageDB(self.db_path, root=str(self.root))
        pid = self.db.add_installed("example", "1.0", release=1, tier="desktop")
        self.db.add_files(pid, [self.PAYLOAD, self.SECOND_PAYLOAD,
                                self.CO_OWNED, self.EDITED_CONFIG])
        self.db.record_generated_files(pid, [self.HOOK_MADE])
        # A second installed package that co-owns one of the paths, so the
        # retained-co-owned class is exercised rather than assumed.
        other = self.db.add_installed("neighbour", "2.0", release=1,
                                      tier="desktop")
        self.db.add_files(other, [self.CO_OWNED])

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _remover(self):
        return PackageRemover(self.db, root=str(self.root))

    def _dry_run(self):
        return self._remover().remove(
            "example", dry_run=True,
            run_pre_remove_hook=False, run_post_remove_hook=False)


class TestTheFlagExists(unittest.TestCase):

    def test_remove_accepts_dry_run(self):
        parser = cli.build_parser()
        args = parser.parse_args(["remove", "example", "--dry-run"])
        self.assertTrue(getattr(args, "remove_dry_run", False),
                        "`pkm remove --dry-run` is not accepted; the one verb "
                        "that can delete a file the package cannot put back "
                        "is the one with no preview")


class TestADryRunChangesNothing(RemovalFixture):

    def test_the_payload_is_still_on_disk(self):
        ok, msg = self._dry_run()
        self.assertTrue(ok, msg)
        self.assertTrue((self.root / self.PAYLOAD).is_file(),
                        "a dry run unlinked the package's payload:\n" + msg)
        self.assertTrue((self.root / self.SECOND_PAYLOAD).is_file(),
                        "a dry run unlinked the package's payload:\n" + msg)

    def test_every_other_recorded_path_is_still_on_disk(self):
        ok, msg = self._dry_run()
        self.assertTrue(ok, msg)
        for rel in (self.CO_OWNED, self.HOOK_MADE, self.EDITED_CONFIG):
            self.assertTrue((self.root / rel).is_file(),
                            f"a dry run unlinked {rel}:\n" + msg)

    def test_the_package_is_still_installed(self):
        ok, msg = self._dry_run()
        self.assertTrue(ok, msg)
        self.assertIsNotNone(
            self.db.get_installed("example"),
            "a dry run removed the database row:\n" + msg)

    def test_the_manifest_file_survives(self):
        manifest_dir = self.root / "var/lib/igos/manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "example-1.0").write_text(self.PAYLOAD + "\n")
        ok, msg = self._dry_run()
        self.assertTrue(ok, msg)
        self.assertTrue((manifest_dir / "example-1.0").exists(),
                        "a dry run deleted the package's manifest:\n" + msg)

    def test_no_remove_hook_fires(self):
        fired = []
        remover = self._remover()
        remover._run_pre_remove_hook = lambda *a, **k: fired.append("pre")
        remover._run_post_remove_hook = lambda *a, **k: fired.append("post")
        ok, msg = remover.remove("example", dry_run=True)
        self.assertTrue(ok, msg)
        self.assertEqual(fired, [],
                         "a dry run ran the package's remove hooks, which are "
                         "allowed to change the machine")


class TestTheReportNamesEachClass(RemovalFixture):

    def test_it_names_the_paths_it_would_unlink(self):
        ok, msg = self._dry_run()
        self.assertTrue(ok, msg)
        self.assertIn(self.PAYLOAD, msg,
                      "the preview did not say which files it would unlink")
        self.assertIn(self.SECOND_PAYLOAD, msg)

    def test_it_names_what_it_would_keep_because_another_package_owns_it(self):
        ok, msg = self._dry_run()
        self.assertTrue(ok, msg)
        self.assertIn(self.CO_OWNED, msg,
                      "the preview said nothing about the co-owned path it "
                      "would retain")
        self.assertIn("neighbour", msg,
                      "the preview did not name the package that co-owns it")

    def test_it_names_what_it_would_keep_because_the_hook_made_it(self):
        ok, msg = self._dry_run()
        self.assertTrue(ok, msg)
        self.assertIn(self.HOOK_MADE, msg,
                      "the preview said nothing about the hook-created row it "
                      "would retain")

    def test_it_says_plainly_that_nothing_was_changed(self):
        ok, msg = self._dry_run()
        self.assertTrue(ok, msg)
        self.assertIn("dry run", msg.lower(),
                      "the preview did not state that nothing was changed")


class TestARealRemovalIsUnchanged(RemovalFixture):
    """The non-masking controls: --dry-run must not have softened removal."""

    def test_without_the_flag_the_payload_is_removed(self):
        ok, msg = self._remover().remove(
            "example", run_pre_remove_hook=False, run_post_remove_hook=False)
        self.assertTrue(ok, msg)
        self.assertFalse((self.root / self.PAYLOAD).exists(),
                         "a real removal left the payload on disk:\n" + msg)

    def test_without_the_flag_the_package_is_gone_from_the_database(self):
        ok, msg = self._remover().remove(
            "example", run_pre_remove_hook=False, run_post_remove_hook=False)
        self.assertTrue(ok, msg)
        self.assertIsNone(self.db.get_installed("example"),
                          "a real removal left the database row:\n" + msg)


if __name__ == "__main__":
    unittest.main()


class TestTheCommandItselfHonoursTheFlag(RemovalFixture):
    """The parser accepting a flag proves nothing; the command must use it."""

    def _run_cli(self, *extra):
        import io
        import contextlib
        from types import SimpleNamespace
        args = SimpleNamespace(package="example", force=False,
                               remove_dry_run=True, verbose=False, quiet=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.cmd_remove(self.db, args)
        return buf.getvalue()

    def test_the_command_leaves_the_payload_on_disk(self):
        out = self._run_cli()
        self.assertTrue((self.root / self.PAYLOAD).is_file(),
                        "`pkm remove --dry-run` unlinked the payload:\n" + out)
        self.assertIsNotNone(self.db.get_installed("example"),
                             "`pkm remove --dry-run` removed the row:\n" + out)

    def test_the_command_prints_the_plan(self):
        out = self._run_cli()
        self.assertIn("would unlink", out,
                      "the command printed no plan:\n" + out)
        self.assertIn(self.PAYLOAD, out)

    def test_the_command_takes_no_restore_point(self):
        called = []
        from pkm import pretxn
        real = pretxn.run_pre_transaction_hook
        pretxn.run_pre_transaction_hook = lambda *a, **k: called.append(a)
        try:
            self._run_cli()
        finally:
            pretxn.run_pre_transaction_hook = real
        self.assertEqual(called, [],
                         "a preview took a restore point, which consumes disk "
                         "for an operation that changes nothing")
