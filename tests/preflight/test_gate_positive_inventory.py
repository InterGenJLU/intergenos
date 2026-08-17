"""Package-tree gates certify only what they POSITIVELY scanned (review finding H4).

Four gates used to shrink their inventory silently (parse failure ->
warn/continue) and to PASS a zero-package scan. A gate exit 0 must mean
"the WHOLE tree was checked and is clean", so: malformed manifest = FAIL
(named), empty inventory = FAIL. Each gate is exercised against a synthetic
tree via its module loaded by file path.
"""

import importlib.util
import io
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

GOOD_SHA = "a" * 64


def _load(script_name, module_name):
    spec = importlib.util.spec_from_file_location(
        module_name, REPO_ROOT / "scripts" / script_name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_pkg(root: Path, tier: str, name: str, body: str | None = None):
    d = root / tier / name
    d.mkdir(parents=True)
    (d / "package.yml").write_text(body if body is not None else textwrap.dedent(f"""\
        name: {name}
        version: '1.0'
        release: 1
        description: d
        license: MIT
        build_style: custom
        tier: {tier}
        source: []
        """))
    return d


class TestTierCoverageInventory(unittest.TestCase):
    def _run(self, packages_dir: Path):
        mod = _load("preflight-tier-coverage.py", "pf_tier_cov_test")
        mod.PACKAGES_DIR = packages_dir
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main()
        return rc, out.getvalue() + err.getvalue()

    def test_malformed_manifest_fails_named(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_pkg(root, "core", "good")
            bad = root / "core" / "bad"
            bad.mkdir(parents=True)
            (bad / "package.yml").write_text("name: [unclosed\n")
            rc, txt = self._run(root)
            self.assertEqual(rc, 1)
            self.assertIn("bad", txt)
            self.assertIn("could not be inventoried", txt)

    def test_missing_tier_fails_not_warns(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_pkg(root, "core", "topless", body="name: topless\nversion: '1'\n")
            rc, txt = self._run(root)
            self.assertEqual(rc, 1)
            self.assertIn("name:/tier:", txt)

    def test_empty_inventory_fails(self):
        with tempfile.TemporaryDirectory() as td:
            rc, txt = self._run(Path(td))
            self.assertEqual(rc, 1)
            self.assertIn("zero packages", txt)


class TestAuditCoverageInventory(unittest.TestCase):
    def _run(self, packages_dir: Path, db_path: Path):
        mod = _load("preflight-audit-coverage.py", "pf_audit_cov_test")
        mod.PACKAGES = packages_dir
        mod.DB = db_path
        out = io.StringIO()
        with redirect_stdout(out):
            rc = mod.main()
        return rc, out.getvalue()

    def _make_db(self, path: Path, rows=()):
        # rows: (name, version, deps_build_json) — the full schema is filled
        # with current-state defaults so only the case under test drives rc.
        import sqlite3
        db = sqlite3.connect(str(path))
        db.execute("CREATE TABLE package_audit (name TEXT, version TEXT, "
                   "tier TEXT, source_sha256 TEXT, "
                   "our_deps_build_json TEXT, our_deps_host_json TEXT, "
                   "our_deps_runtime_json TEXT, our_patches_json TEXT, "
                   "our_autotools_flags_json TEXT, our_meson_options_json TEXT, "
                   "audited_at TEXT, audited_by TEXT)")
        for (name, version, deps_json, *_rest) in rows:
            db.execute("INSERT INTO package_audit VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (name, version, "core", None, deps_json,
                        "[]", "[]", "[]", "[]", "[]", "t", "a"))
        db.commit()
        db.close()

    def test_malformed_manifest_fails_named(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkgs = root / "packages"
            _write_pkg(pkgs, "core", "good")
            bad = pkgs / "core" / "bad"
            bad.mkdir(parents=True)
            (bad / "package.yml").write_text("just a string\n")
            db = root / "audit.db"
            self._make_db(db, [("good", "1.0", "[]", "t", "a")])
            rc, txt = self._run(pkgs, db)
            self.assertEqual(rc, 1)
            self.assertIn("could not be inventoried", txt)

    def test_zero_scope_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkgs = root / "packages"
            pkgs.mkdir()
            db = root / "audit.db"
            self._make_db(db)
            rc, txt = self._run(pkgs, db)
            self.assertEqual(rc, 1)
            self.assertIn("zero packages in audit scope", txt)


class TestIsoClosureInventory(unittest.TestCase):
    def _run(self, packages_dir: Path):
        mod = _load("preflight-iso-closure.py", "pf_iso_closure_test")
        findings, stats = mod.scan(REPO_ROOT, packages_dir)
        return findings

    def test_parse_failure_is_hard_finding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_pkg(root, "core", "good")
            bad = root / "core" / "bad"
            bad.mkdir(parents=True)
            (bad / "package.yml").write_text(
                "name: bad\nversion: '1'\nrelease: 1\ndescription: d\n"
                "license: MIT\nbuild_style: nosuchstyle\ntier: core\nsource: []\n")
            findings = self._run(root)
            self.assertTrue(any(f["type"] == "ISO-CLOSURE-PARSE-FAILURE"
                                for f in findings),
                            f"expected a hard parse finding, got {findings}")

    def test_empty_inventory_is_finding(self):
        with tempfile.TemporaryDirectory() as td:
            findings = self._run(Path(td))
            self.assertTrue(any(f["type"] == "ISO-CLOSURE-EMPTY-INVENTORY"
                                for f in findings))


class TestValidateTiersInventory(unittest.TestCase):
    def test_empty_tree_exits_2(self):
        mod = _load("validate-package-tiers.py", "vpt_inventory_test")
        with tempfile.TemporaryDirectory() as td:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = mod.main(["validate-package-tiers.py",
                               "--packages-dir", td])
            self.assertEqual(rc, 2)
            self.assertIn("empty scan validates nothing", err.getvalue())


if __name__ == "__main__":
    unittest.main()
