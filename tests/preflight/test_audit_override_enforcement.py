""".audit-override files are enforced waivers, not existence bypasses (review finding H3).

The documented contract — JSON {reason, approved_by, expires_at} — was
never parsed: any file (even empty) silently exempted its package from the
audit gate forever. Now the contract is required and an expired override
is a gate FAILURE.
"""

import importlib.util
import io
import json
import sqlite3
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "pf_audit_override_test", REPO_ROOT / "scripts" / "preflight-audit-coverage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestAuditOverrideEnforcement(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.pkgs = self.tmp / "packages"
        self.pkg_dir = self.pkgs / "core" / "demo"
        self.pkg_dir.mkdir(parents=True)
        (self.pkg_dir / "package.yml").write_text(textwrap.dedent("""\
            name: demo
            version: '1.0'
            tier: core
            """))
        self.db = self.tmp / "audit.db"
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE package_audit (name TEXT, version TEXT, "
                     "tier TEXT, source_sha256 TEXT, "
                     "our_deps_build_json TEXT, our_deps_host_json TEXT, "
                     "our_deps_runtime_json TEXT, our_patches_json TEXT, "
                     "our_autotools_flags_json TEXT, our_meson_options_json TEXT, "
                     "audited_at TEXT, audited_by TEXT)")
        conn.commit()
        conn.close()
        # NOTE: no audit row for 'demo' — without a working override the
        # gate fails MISSING, so the override is load-bearing in every test.

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self):
        mod = _load()
        mod.PACKAGES = self.pkgs
        mod.DB = self.db
        out = io.StringIO()
        with redirect_stdout(out):
            rc = mod.main()
        return rc, out.getvalue()

    def _write_override(self, content):
        (self.pkg_dir / ".audit-override").write_text(content)

    def test_valid_override_passes_and_is_reported(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        self._write_override(json.dumps({
            "reason": "upstream audit pending refresh",
            "approved_by": "maintainer",
            "expires_at": future}))
        rc, txt = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("upstream audit pending refresh", txt)
        self.assertIn(future, txt)

    def test_empty_file_is_gate_failure(self):
        # The historical shape: a bare touch(1) file waived the audit forever.
        self._write_override("")
        rc, txt = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("UNUSABLE", txt)

    def test_missing_field_is_gate_failure(self):
        self._write_override(json.dumps({"reason": "r", "approved_by": ""}))
        rc, txt = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("approved_by", txt)

    def test_expired_override_is_gate_failure(self):
        past = (date.today() - timedelta(days=1)).isoformat()
        self._write_override(json.dumps({
            "reason": "r", "approved_by": "m", "expires_at": past}))
        rc, txt = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("EXPIRED", txt)

    def test_malformed_date_is_gate_failure(self):
        self._write_override(json.dumps({
            "reason": "r", "approved_by": "m", "expires_at": "sometime"}))
        rc, txt = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("YYYY-MM-DD", txt)


if __name__ == "__main__":
    unittest.main()
