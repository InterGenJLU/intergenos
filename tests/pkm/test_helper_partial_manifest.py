#!/usr/bin/env python3
"""Tests for H-007 Decision D: helper crash partial-manifest sidecar.

When a helper invokes igos_helper_init + record_* calls but crashes
before igos_helper_commit, the EXIT trap installed by init writes a
`<name>.manifest.partial` JSON sidecar at HELPER_MANIFEST_DIR. pkm's
reader at _run_helper detects the sidecar + surfaces the orphan file
list to the user in the install-failed error message.

Coverage:
  - _read_partial_manifest_summary: 4 unit cases (absent / valid /
    malformed JSON / non-dict root)
  - helper-lib.sh end-to-end: bash subprocess invokes a crashing
    helper + verifies the sidecar exists with the expected shape
  - helper-lib.sh end-to-end: bash subprocess invokes a successful
    helper + verifies no partial sidecar remains
  - helper-lib.sh end-to-end: crash followed by successful re-run
    leaves only the canonical manifest (no stale sidecar)
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pkm.installer
from pkm.installer import _read_partial_manifest_summary

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HELPER_LIB_SH = REPO_ROOT / "packages/core/intergenos-helper-lib/helper-lib.sh"


class ReadPartialManifestSummaryTests(unittest.TestCase):
    """Unit tests for the partial-manifest sidecar reader."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self._patcher = patch.object(pkm.installer, "HELPER_MANIFEST_DIR", self.tmp)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_absent_sidecar_returns_none(self):
        self.assertIsNone(_read_partial_manifest_summary("nopkg"))

    def test_valid_sidecar_returns_summary(self):
        payload = {
            "version": 1,
            "name": "demo",
            "version_installed": "3.4.5",
            "files": ["/opt/demo/bin", "/usr/share/demo/data"],
            "symlinks": [{"path": "/usr/local/bin/demo", "target": "/opt/demo/bin"}],
            "depends": ["glibc"],
            "post_install_actions_log": [],
            "partial": True,
            "build_date": "2026-05-19T19:00:00Z",
        }
        (self.tmp / "demo.manifest.partial").write_text(json.dumps(payload))
        result = _read_partial_manifest_summary("demo")
        self.assertIsNotNone(result)
        self.assertEqual(result["count"], 3)
        self.assertEqual(
            result["sample"],
            ["/opt/demo/bin", "/usr/share/demo/data", "/usr/local/bin/demo"],
        )
        self.assertEqual(result["version_installed"], "3.4.5")
        self.assertEqual(result["path"], self.tmp / "demo.manifest.partial")

    def test_malformed_json_returns_none(self):
        (self.tmp / "bogus.manifest.partial").write_text("not json {{{")
        self.assertIsNone(_read_partial_manifest_summary("bogus"))

    def test_non_dict_root_returns_none(self):
        (self.tmp / "weird.manifest.partial").write_text(json.dumps([1, 2, 3]))
        self.assertIsNone(_read_partial_manifest_summary("weird"))


@unittest.skipUnless(
    HELPER_LIB_SH.is_file(),
    "helper-lib.sh end-to-end tests need the library file in tree",
)
class HelperLibPartialManifestEndToEndTests(unittest.TestCase):
    """Integration tests subprocess-invoking helper-lib.sh from bash."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.manifest_dir = self.tmp / "manifests"
        self.manifest_dir.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _run(self, script_body):
        """Run a bash script body in a subprocess with the helper-lib sourced.

        Returns CompletedProcess. Caller asserts returncode + checks
        the manifest dir for produced files.
        """
        script = f"""
set +e
source {HELPER_LIB_SH}
{script_body}
"""
        env = os.environ.copy()
        env["IGOS_HELPER_MANIFEST_DIR"] = str(self.manifest_dir)
        return subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_crash_after_records_writes_partial_sidecar(self):
        body = """
igos_helper_init "crashpkg"
igos_helper_set_version "1.0.0"
igos_helper_record_file /opt/crashpkg/bin
igos_helper_record_file /opt/crashpkg/lib/libfoo.so
igos_helper_record_dep glibc
exit 1
"""
        proc = self._run(body)
        self.assertEqual(proc.returncode, 1)
        partial = self.manifest_dir / "crashpkg.manifest.partial"
        canonical = self.manifest_dir / "crashpkg.manifest"
        self.assertTrue(partial.is_file(), f"partial sidecar missing: {proc.stderr}")
        self.assertFalse(canonical.is_file(), "canonical manifest must NOT exist on crash")
        data = json.loads(partial.read_text())
        self.assertEqual(data["name"], "crashpkg")
        self.assertEqual(data["version_installed"], "1.0.0")
        self.assertEqual(
            data["files"],
            ["/opt/crashpkg/bin", "/opt/crashpkg/lib/libfoo.so"],
        )
        self.assertEqual(data["depends"], ["glibc"])
        self.assertTrue(data.get("partial"), "partial flag must be True in sidecar")

    def test_successful_commit_writes_canonical_not_partial(self):
        body = """
igos_helper_init "okpkg"
igos_helper_set_version "2.0.0"
igos_helper_record_file /opt/okpkg/bin
igos_helper_record_dep glibc
igos_helper_commit
"""
        proc = self._run(body)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        partial = self.manifest_dir / "okpkg.manifest.partial"
        canonical = self.manifest_dir / "okpkg.manifest"
        self.assertTrue(canonical.is_file())
        self.assertFalse(partial.is_file(), "no partial sidecar after successful commit")
        data = json.loads(canonical.read_text())
        self.assertNotIn("partial", data)
        self.assertEqual(data["files"], ["/opt/okpkg/bin"])

    def test_user_cleanup_runs_on_crash_before_sidecar(self):
        # BLOCKING-D fix verification: helper sets IGOS_HELPER_USER_CLEANUP
        # instead of installing its own `trap ... EXIT`. On crash, the
        # cleanup runs + the partial sidecar still gets written.
        sentinel = self.tmp / "user-cleanup-ran-on-crash"
        body = f"""
IGOS_HELPER_USER_CLEANUP="touch {sentinel}"
igos_helper_init "ucleanup-crashpkg"
igos_helper_set_version "1.0.0"
igos_helper_record_file /opt/ucleanup-crashpkg/bin
exit 1
"""
        proc = self._run(body)
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(
            sentinel.exists(),
            f"user cleanup must run on crash; sentinel missing: {proc.stderr}",
        )
        partial = self.manifest_dir / "ucleanup-crashpkg.manifest.partial"
        self.assertTrue(partial.is_file(), "sidecar must still be written on crash")

    def test_user_cleanup_runs_on_successful_commit(self):
        # BLOCKING-D fix verification: trap stays installed even after
        # commit; commit sets IGOS_HELPER_COMMITTED=1 which short-
        # circuits the sidecar write but still runs user cleanup at
        # script exit.
        sentinel = self.tmp / "user-cleanup-ran-on-success"
        body = f"""
IGOS_HELPER_USER_CLEANUP="touch {sentinel}"
igos_helper_init "ucleanup-okpkg"
igos_helper_set_version "2.0.0"
igos_helper_record_file /opt/ucleanup-okpkg/bin
igos_helper_commit
"""
        proc = self._run(body)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertTrue(
            sentinel.exists(),
            f"user cleanup must run on successful commit; sentinel missing: {proc.stderr}",
        )
        canonical = self.manifest_dir / "ucleanup-okpkg.manifest"
        self.assertTrue(canonical.is_file(), "canonical manifest must be written")
        partial = self.manifest_dir / "ucleanup-okpkg.manifest.partial"
        self.assertFalse(partial.is_file(), "no sidecar on success")

    def test_crash_then_successful_retry_supersedes_partial(self):
        crash_body = """
igos_helper_init "retrypkg"
igos_helper_set_version "1.0.0"
igos_helper_record_file /opt/retrypkg/old-bin
exit 1
"""
        success_body = """
igos_helper_init "retrypkg"
igos_helper_set_version "1.0.1"
igos_helper_record_file /opt/retrypkg/new-bin
igos_helper_commit
"""
        crash_proc = self._run(crash_body)
        self.assertEqual(crash_proc.returncode, 1)
        partial_after_crash = self.manifest_dir / "retrypkg.manifest.partial"
        self.assertTrue(partial_after_crash.is_file())

        success_proc = self._run(success_body)
        self.assertEqual(success_proc.returncode, 0, msg=success_proc.stderr)
        partial = self.manifest_dir / "retrypkg.manifest.partial"
        canonical = self.manifest_dir / "retrypkg.manifest"
        self.assertTrue(canonical.is_file())
        self.assertFalse(
            partial.is_file(),
            "successful retry must clean prior partial sidecar",
        )
        data = json.loads(canonical.read_text())
        self.assertEqual(data["files"], ["/opt/retrypkg/new-bin"])
        self.assertEqual(data["version_installed"], "1.0.1")


if __name__ == "__main__":
    unittest.main()
