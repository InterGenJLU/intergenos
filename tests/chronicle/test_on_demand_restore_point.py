#!/usr/bin/env python3
"""An on-demand restore point is captured, not refused.

WHAT WAS WRONG (a real install, 2026-10-16). The engine accepted a
restore-point capture only with a pkm transaction's footprint dict as its
scope and refused everything else: "restore-point capture requires a footprint
dict as scope". So the installer's restore point of the system as installed
(`chronicle capture restore-point --json --reason ...`, run in the target at
install end) failed on every install, and the command the health check tells a
person to run when no restore point exists failed the same way.

WHAT THIS PROVES, against the real engine and the real command line over a
throwaway store: a capture with no scope, or with the CLI's PATH list, is a
restore point of those paths plus the package database, tagged with the
reason it was given and readable back from the timeline; a relative path is
refused; and pkm's own footprint dict is captured exactly as before.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from chronicle import cli as _cli
from chronicle import engine as _engine
from chronicle import paths as _paths
from chronicle import restorepoint as _restorepoint


class OnDemandRestorePoint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="chronicle-ondemand-"))
        self.local = self.tmp / "local"
        self.db = self.tmp / "var/lib/igos/pkm.db"
        self.db.parent.mkdir(parents=True)
        self.db.write_bytes(b"SQLite format 3\0 the package database as installed")
        # create=True so the unchanged-behaviour case also runs on a tree
        # that predates the constant.
        patcher = mock.patch.object(_restorepoint, "PKM_DB_PATH", str(self.db),
                                    create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = [2_000_000.0]
        self.eng = _engine.Engine(local_root=str(self.local),
                                  now_fn=lambda: self.now[0])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _manifest(self, vid):
        return self.eng.get_manifest(_paths.LAYER_RESTORE_POINT, vid)

    def test_no_scope_captures_the_package_database_with_its_reason(self):
        out = self.eng.capture(_paths.LAYER_RESTORE_POINT,
                               reason="the system as installed")
        vid = out["version_id"]
        versions = self.eng.list_versions(_paths.LAYER_RESTORE_POINT)
        self.assertEqual([(v["version_id"], v["reason"]) for v in versions],
                         [(vid, "the system as installed")])
        m = self._manifest(vid)
        self.assertEqual([e["path"] for e in m["entries"]], [str(self.db)])
        self.assertEqual(m["restore_point"]["verb"], "on-demand")
        self.assertEqual(m["restore_point"]["absent_paths"], [])
        self.assertTrue(self.eng.verify(_paths.LAYER_RESTORE_POINT, vid).get("ok"))

    def test_named_paths_are_captured_beside_the_database(self):
        conf = self.tmp / "etc/app.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text("key = 1\n")
        vid = self.eng.capture(_paths.LAYER_RESTORE_POINT, scope=[str(conf)],
                               reason="before an edit")["version_id"]
        self.assertEqual(sorted(e["path"] for e in self._manifest(vid)["entries"]),
                         sorted([str(conf), str(self.db)]))

    def test_a_relative_path_is_refused(self):
        with self.assertRaises(_engine.EngineError) as ctx:
            self.eng.capture(_paths.LAYER_RESTORE_POINT, scope=["etc/app.conf"],
                             reason="x")
        self.assertIn("absolute paths", str(ctx.exception))
        self.assertEqual(self.eng.list_versions(_paths.LAYER_RESTORE_POINT), [])

    def test_a_pkm_footprint_is_captured_as_before(self):
        footprint = {"verb": "install", "reason": "pkm install hello",
                     "packages": ["hello"], "paths": [], "db_path": str(self.db)}
        vid = self.eng.capture(_paths.LAYER_RESTORE_POINT, scope=footprint,
                               reason="pkm install hello")["version_id"]
        m = self._manifest(vid)
        self.assertEqual(m["reason"], "pkm install hello")
        self.assertEqual(m["restore_point"]["verb"], "install")
        self.assertEqual(m["restore_point"]["packages"], ["hello"])

    def test_the_command_line_the_installer_runs_succeeds_and_reads_back(self):
        def cli(argv):
            out, err = io.StringIO(), io.StringIO()
            full = ["--local-root", str(self.local), "--socket",
                    str(self.tmp / "no-engine.sock")] + argv
            with redirect_stdout(out), redirect_stderr(err):
                rc = _cli.main(full)
            return rc, out.getvalue(), err.getvalue()

        rc, out, err = cli(["capture", "restore-point", "--json",
                            "--reason", "the system as installed"])
        self.assertEqual(rc, 0, out + err)
        vid = json.loads(out)["version_id"]
        rc, out, err = cli(["list", "restore-point", "--json"])
        self.assertEqual(rc, 0, out + err)
        listed = json.loads(out)
        self.assertIn((vid, "the system as installed"),
                      [(v["version_id"], v.get("reason")) for v in listed])


if __name__ == "__main__":
    unittest.main()
