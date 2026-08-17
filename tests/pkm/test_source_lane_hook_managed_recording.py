# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The source-build lane records what a recipe's post_install rewrote.

The archive install path observes its own hook by snapshotting around it. The
source-build lane cannot: its manifest is written from the PRISTINE staging
tree and its rows are registered before the driver ever runs the recipe's
post_install, so a payload file the hook rewrites in place keeps a hash that
can never match again. Every later check reads that as damage, and the image
metadata-sync gate refuses a correct build.

These cases pin the two halves that close it — a baseline captured before the
hook, and a comparison after it — and, just as importantly, pin what must NOT
happen: a file another package owns is never reclassified, and a file that
merely disagrees with its recorded hash is never reclassified either. The
second is the load-bearing refusal. Reclassifying on disagreement would stop
the byte check on exactly the file most likely to be damaged; only a change
observed across the post_install window is evidence that a hook did it.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pkm.database import PackageDB
from pkm.installer import PackageInstaller


def _touch(path: Path, content: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class _Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "t.db", root=str(self.root))
        self.installer = PackageInstaller(self.db, root=str(self.root))
        self.manifest_dir = self.root / "var" / "lib" / "igos" / "packages"

    def tearDown(self):
        self.db.close()

    def _register(self, name, files, version="1.0"):
        """Register a package the way the source lane does: from staging bytes."""
        for rel, content in files.items():
            _touch(self.root / rel, content)
        pkg_id = self.db.add_installed(
            name=name, version=version, release=1,
            install_method="source-build")
        self.db.add_files(pkg_id, list(files))
        return pkg_id

    def _row(self, pkg, path):
        return self.db.conn.execute(
            "SELECT f.is_generated FROM files f JOIN installed i "
            "ON f.package_id = i.id WHERE i.name = ? AND f.path = ?",
            (pkg, path),
        ).fetchone()


class OwnPayloadRecordedTests(_Harness):

    def test_a_file_the_hook_rewrote_is_recorded_as_hook_managed(self):
        self._register("demo", {"usr/share/demo/catalog": "pristine\n"})
        baseline = self.installer.hook_baseline("demo")
        self.assertIn("usr/share/demo/catalog", baseline,
                      "precondition: the baseline must cover the own payload")

        # The recipe's post_install rewrites its own deployed file.
        (self.root / "usr/share/demo/catalog").write_text("rewritten\n")

        changed, messages = self.installer.record_hook_changes(
            "demo", baseline)
        self.assertEqual(changed, ["usr/share/demo/catalog"])
        self.assertEqual(
            self._row("demo", "usr/share/demo/catalog")[0], 1,
            "a payload file the package's own post_install rewrote must be "
            "recorded as hook-managed, or every later check reports designed "
            "behaviour as damage")
        self.assertTrue(messages, "the recording must say what it did")

    def test_the_manifest_states_the_class_so_an_import_can_carry_it(self):
        self._register("demo", {"usr/share/demo/catalog": "pristine\n"})
        baseline = self.installer.hook_baseline("demo")
        (self.root / "usr/share/demo/catalog").write_text("rewritten\n")
        self.installer.record_hook_changes("demo", baseline)

        manifest = (self.manifest_dir / "demo-1.0").read_text()
        self.assertIn(
            "HOOK-MANAGED: usr/share/demo/catalog", manifest,
            "the row alone cannot survive a from-scratch import — the "
            "manifest must state the class or the shipped image registers the "
            "file as ordinary payload again")

        fresh = PackageDB(self.tmp / "fresh.db", root=str(self.root))
        try:
            fresh.import_manifests(manifest_dir=str(self.manifest_dir))
            row = fresh.conn.execute(
                "SELECT f.is_generated FROM files f JOIN installed i "
                "ON f.package_id = i.id "
                "WHERE i.name = 'demo' AND f.path = ?",
                ("usr/share/demo/catalog",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(
                row[0], 1,
                "end to end: what the producer states, the import carries")
        finally:
            fresh.close()

    def test_an_untouched_payload_file_is_not_reclassified(self):
        self._register("demo", {
            "usr/share/demo/catalog": "pristine\n",
            "usr/share/demo/data": "untouched\n",
        })
        baseline = self.installer.hook_baseline("demo")
        (self.root / "usr/share/demo/catalog").write_text("rewritten\n")
        changed, _ = self.installer.record_hook_changes("demo", baseline)
        self.assertEqual(changed, ["usr/share/demo/catalog"])
        self.assertEqual(
            self._row("demo", "usr/share/demo/data")[0], 0,
            "only what the hook actually changed may be reclassified")

    def test_no_change_records_nothing(self):
        self._register("demo", {"usr/share/demo/catalog": "pristine\n"})
        baseline = self.installer.hook_baseline("demo")
        changed, messages = self.installer.record_hook_changes(
            "demo", baseline)
        self.assertEqual(changed, [])
        self.assertEqual(messages, [])
        self.assertEqual(self._row("demo", "usr/share/demo/catalog")[0], 0)


class RecordingRefusalTests(_Harness):
    """What the mechanism must refuse to do."""

    def test_a_file_another_package_owns_is_never_reclassified(self):
        self._register("victim", {"usr/share/victim/data": "victim bytes\n"})
        self._register("demo", {"usr/share/demo/own": "own\n"})

        baseline = self.installer.hook_baseline("demo")
        # The hook reaches outside its own payload and rewrites the victim's
        # file. That is a real event and the victim's check must keep
        # reporting it; it is not this package's content to reclassify.
        (self.root / "usr/share/victim/data").write_text("overwritten\n")

        changed, _ = self.installer.record_hook_changes("demo", baseline)
        self.assertEqual(
            changed, [],
            "a hook writing ANOTHER package's file must never launder that "
            "file into a classification that stops checking its content")
        self.assertEqual(
            self._row("victim", "usr/share/victim/data")[0], 0)
        result = self.db.verify_package("victim")
        self.assertEqual(
            [p.lstrip("/") for p in result["modified"]],
            ["usr/share/victim/data"],
            "the victim's byte check must stay exactly as strict")

    def test_a_baseline_naming_a_foreign_path_cannot_reclassify_it(self):
        """The ownership check itself, exercised directly.

        The previous case proves the OUTCOME, but not this mechanism: a
        baseline taken for `demo` never contains another package's path, so
        the loop never reaches the ownership test and the case passes with
        that test removed (verified by mutation). This one hands
        record_hook_changes a baseline that DOES name a foreign path — the
        condition the check exists for, where the rows moved between the two
        halves — so the refusal has to do the work.
        """
        self._register("victim", {"usr/share/victim/data": "victim bytes\n"})
        self._register("demo", {"usr/share/demo/own": "own\n"})

        baseline = self.installer.hook_baseline("demo")
        baseline["usr/share/victim/data"] = "0" * 64  # not demo's to classify
        (self.root / "usr/share/victim/data").write_text("overwritten\n")

        changed, _ = self.installer.record_hook_changes("demo", baseline)
        self.assertEqual(
            changed, [],
            "a path this package does not own must be refused even when the "
            "baseline names it — ownership is what stops this mechanism "
            "becoming a way to silence another package's byte check")
        self.assertEqual(
            self._row("victim", "usr/share/victim/data")[0], 0)

    def test_divergence_without_the_window_is_never_reclassified(self):
        """The refusal that matters most: damage is not evidence of a hook."""
        self._register("demo", {"usr/share/demo/catalog": "pristine\n"})
        # The file is damaged BEFORE the baseline is taken — so it disagrees
        # with its recorded checksum, but nothing changed across the hook.
        (self.root / "usr/share/demo/catalog").write_text("tampered\n")
        baseline = self.installer.hook_baseline("demo")

        # post_install runs and touches nothing.
        changed, _ = self.installer.record_hook_changes("demo", baseline)

        self.assertEqual(
            changed, [],
            "a file that merely disagrees with its recorded hash must NEVER "
            "be reclassified — inferring hook-managed from divergence would "
            "silence the byte check on a damaged file, which is the exact "
            "mask-rather-than-verify shape this mechanism exists to remove")
        self.assertEqual(
            self._row("demo", "usr/share/demo/catalog")[0], 0)
        result = self.db.verify_package("demo")
        self.assertEqual(
            [p.lstrip("/") for p in result["modified"]],
            ["usr/share/demo/catalog"],
            "and verify must still report it")

    def test_an_unregistered_package_is_reported_not_guessed_at(self):
        changed, messages = self.installer.record_hook_changes(
            "nosuch", {"usr/share/x": "0" * 64})
        self.assertEqual(changed, [])
        self.assertTrue(
            any("not registered" in m for m in messages),
            "say so rather than silently doing nothing")


if __name__ == "__main__":
    unittest.main()
