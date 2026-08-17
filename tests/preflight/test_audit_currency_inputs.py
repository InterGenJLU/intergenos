"""Audit currency covers EVERY recorded input, not version + build deps (review finding H2).

The audit schema records tier, source sha, host/runtime deps, patches and
configure flags — but the gate compared only version + our_deps_build_json,
so a same-version change to any other input kept a stale PASS. Each test
drifts exactly one input and expects a named stale-inputs failure.
"""

import importlib.util
import io
import json
import sqlite3
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

COLUMNS = ("name", "version", "tier", "source_sha256", "our_deps_build_json",
           "our_deps_host_json", "our_deps_runtime_json", "our_patches_json",
           "our_autotools_flags_json", "our_meson_options_json",
           "audited_at", "audited_by")


def _load():
    spec = importlib.util.spec_from_file_location(
        "pf_audit_currency_test",
        REPO_ROOT / "scripts" / "preflight-audit-coverage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestAuditCurrencyInputs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.pkgs = self.tmp / "packages"
        self.pkg_dir = self.pkgs / "core" / "demo"
        self.pkg_dir.mkdir(parents=True)
        self.db = self.tmp / "audit.db"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_yml(self, runtime=("libx",), sha="a" * 64, tier="core",
                   patches=()):
        patches_yaml = ("patches:\n" + "".join(f"  - {p}\n" for p in patches)
                        if patches else "")
        runtime_yaml = ("  runtime:\n" + "".join(f"    - {r}\n" for r in runtime)
                        if runtime else "")
        (self.pkg_dir / "package.yml").write_text(textwrap.dedent(f"""\
            name: demo
            version: '1.0'
            tier: {tier}
            source:
              - url: https://x/demo-1.0.tar.gz
                sha256: {sha}
            dependencies:
              build:
                - gcc
            """) + runtime_yaml + patches_yaml)

    def _write_db(self, runtime=("libx",), sha="a" * 64, tier="core",
                  patches=()):
        conn = sqlite3.connect(str(self.db))
        conn.execute(f"CREATE TABLE package_audit ({', '.join(c + ' TEXT' for c in COLUMNS)})")
        conn.execute(
            f"INSERT INTO package_audit VALUES ({','.join('?' * len(COLUMNS))})",
            ("demo", "1.0", tier, sha, json.dumps(["gcc"]),
             "[]", json.dumps(list(runtime)), json.dumps(list(patches)),
             "[]", "[]", "t", "a"))
        conn.commit()
        conn.close()

    def _run(self):
        mod = _load()
        mod.PACKAGES = self.pkgs
        mod.DB = self.db
        out = io.StringIO()
        with redirect_stdout(out):
            rc = mod.main()
        return rc, out.getvalue()

    def test_fully_current_audit_passes(self):
        self._write_yml()
        self._write_db()
        rc, txt = self._run()
        self.assertEqual(rc, 0, txt)

    def test_runtime_dep_added_after_audit_fails(self):
        # The live 402-package shape: audits from May, runtime edges added
        # for ISO closure in July — same version, silent under the old gate.
        self._write_yml(runtime=("libx", "libnew"))
        self._write_db(runtime=("libx",))
        rc, txt = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("STALE AUDIT INPUTS", txt)
        self.assertIn("deps_runtime", txt)

    def test_source_pin_swap_fails(self):
        self._write_yml(sha="b" * 64)
        self._write_db(sha="a" * 64)
        rc, txt = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("source_sha256", txt)

    def test_retier_fails(self):
        self._write_yml(tier="base")
        self._write_db(tier="core")
        rc, txt = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("tier", txt)

    def test_patch_added_after_audit_fails(self):
        self._write_yml(patches=("fix-cve.patch",))
        self._write_db(patches=())
        rc, txt = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("patches", txt)

    def test_configure_flag_added_after_audit_fails(self):
        self._write_yml()
        (self.pkg_dir / "build.sh").write_text(textwrap.dedent("""\
            configure() {
                ./configure --enable-new-feature
            }
            """))
        self._write_db()  # audit recorded zero flags
        rc, txt = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("configure flags", txt)


if __name__ == "__main__":
    unittest.main()
