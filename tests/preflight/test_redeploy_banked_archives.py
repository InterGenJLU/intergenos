"""redeploy-banked-archives: classification + healing-path coverage.

Fixture = a miniature chroot (sealed archives with real ./.PKGINFO members, a
real-schema sqlite pkm DB subset, text manifests) + a miniature recipe tree.
Covers every verdict class, ships_as resolution, exit codes, and the apply
path's command construction (subprocess patched — the real chroot firing is
the build VM's deferred proof leg).
"""

import importlib.util
import io
import json
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "redeploy_banked_test", REPO_ROOT / "scripts" / "redeploy-banked-archives.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_archive(archives_dir: Path, pkgname: str, pkgver: str, pkgrel: int):
    pkginfo = f"pkgname = {pkgname}\npkgver = {pkgver}\npkgrel = {pkgrel}\n"
    out = archives_dir / f"{pkgname}-{pkgver}.igos.tar.gz"
    if out.exists():
        out = archives_dir / f"{pkgname}-{pkgver}-r{pkgrel}.igos.tar.gz"
    with tarfile.open(out, "w:gz") as tf:
        data = pkginfo.encode()
        ti = tarfile.TarInfo("./.PKGINFO")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    return out


class RedeployBankedArchivesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="redeploy-test.")
        base = Path(self._tmp.name)
        self.chroot = base / "chroot"
        self.archives = self.chroot / "var/lib/igos/archives"
        self.manifests = self.chroot / "var/lib/igos/packages"
        self.dbdir = self.chroot / "var/lib/pkm"
        for d in (self.archives, self.manifests, self.dbdir):
            d.mkdir(parents=True)
        db = sqlite3.connect(self.dbdir / "pkm.db")
        db.execute("CREATE TABLE installed (id INTEGER PRIMARY KEY, name TEXT, "
                   "version TEXT, release INTEGER DEFAULT 1, superseded_by TEXT)")
        db.commit()
        db.close()
        self.packages = base / "packages"
        self.packages.mkdir()
        self.mod = _load()

    def tearDown(self):
        self._tmp.cleanup()

    def add_recipe(self, tier, name, version, release=1, ships_as=None):
        d = self.packages / tier / name
        d.mkdir(parents=True)
        body = f'name: {name}\nversion: "{version}"\nrelease: {release}\n'
        if ships_as:
            body += f"ships_as: {ships_as}\n"
        (d / "package.yml").write_text(body)

    def mark_installed(self, name, version, release=1):
        db = sqlite3.connect(self.dbdir / "pkm.db")
        db.execute("INSERT INTO installed (name, version, release) VALUES (?,?,?)",
                   (name, version, release))
        db.commit()
        db.close()
        (self.manifests / f"{name}-{version}").write_text("")

    def run_main(self, *extra):
        argv = ["redeploy-banked-archives.py",
                "--chroot", str(self.chroot),
                "--packages-dir", str(self.packages), *extra]
        buf = io.StringIO()
        with mock.patch("sys.argv", argv), redirect_stdout(buf):
            rc = self.mod.main()
        return rc, buf.getvalue()

    def test_deployed_current_is_clean_exit_0(self):
        self.add_recipe("core", "zlib", "1.3.1", 2)
        make_archive(self.archives, "zlib", "1.3.1", 2)
        self.mark_installed("zlib", "1.3.1", 2)
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertIn("clean (exit 0)", out)

    def test_banked_not_deployed_current_joins_redeploy_exit_2(self):
        self.add_recipe("core", "lib32-wayland", "1.24.0", 1)
        make_archive(self.archives, "lib32-wayland", "1.24.0", 1)
        rc, out = self.run_main()
        self.assertEqual(rc, 2, out)
        self.assertIn("BANKED-NOT-DEPLOYED (current, healable): lib32-wayland", out)

    def test_banked_not_deployed_tree_ahead_joins_rebuild(self):
        self.add_recipe("core", "attr", "2.5.2", 3)
        make_archive(self.archives, "attr", "2.5.2", 2)
        rc, out = self.run_main()
        self.assertEqual(rc, 2, out)
        self.assertIn("BANKED-NOT-DEPLOYED (stale): attr", out)
        self.assertNotIn("healable): attr", out)

    def test_deployed_stale_reported_as_rebuild(self):
        self.add_recipe("core", "acl", "2.3.2", 5)
        make_archive(self.archives, "acl", "2.3.2", 4)
        self.mark_installed("acl", "2.3.2", 4)
        rc, out = self.run_main()
        self.assertEqual(rc, 2, out)
        self.assertIn("DEPLOYED-STALE: acl", out)

    def test_superseded_twin_reported_newest_wins(self):
        self.add_recipe("extra", "go", "1.26.4", 1)
        make_archive(self.archives, "go", "1.26.2", 1)
        make_archive(self.archives, "go", "1.26.4", 1)
        self.mark_installed("go", "1.26.4", 1)
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertIn("SUPERSEDED-TWIN", out)
        self.assertIn("go-1.26.2", out)

    def test_ships_as_twin_resolves_to_ship_identity(self):
        # Recipe named glibc-pass2 ships as glibc; the banked archive carries
        # the SHIP name. Classification must find the recipe via ships_as.
        self.add_recipe("toolchain", "glibc-pass2", "2.41", 1, ships_as="glibc")
        make_archive(self.archives, "glibc", "2.41", 1)
        rc, out = self.run_main()
        self.assertEqual(rc, 2, out)
        self.assertIn("healable): glibc", out)
        self.assertNotIn("NO-RECIPE", out)

    def test_toolchain_intermediate_skipped_by_design(self):
        make_archive(self.archives, "gcc-pass1", "14.2.0", 1)
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertIn("skipped by design", out)

    def test_no_recipe_archive_is_a_loud_finding(self):
        make_archive(self.archives, "mystery-blob", "1.0", 1)
        rc, out = self.run_main()
        self.assertEqual(rc, 2, out)
        self.assertIn("NO-RECIPE", out)

    def test_apply_runs_install_then_verify_and_reports(self):
        self.add_recipe("core", "lib32-libffi", "3.5.2", 1)
        make_archive(self.archives, "lib32-libffi", "3.5.2", 1)
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(self.mod.subprocess, "run", side_effect=fake_run):
            rc, out = self.run_main("--apply")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(calls), 2)
        self.assertIn("pkm", calls[0])
        self.assertIn("--archive-trust", calls[0])
        self.assertIn("verify", calls[1])
        self.assertIn("1/1 healed, 0 failed", out)

    def test_apply_failure_is_nonzero_and_named(self):
        self.add_recipe("core", "libxcrypt", "4.4.38", 1)
        make_archive(self.archives, "libxcrypt", "4.4.38", 1)

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        with mock.patch.object(self.mod.subprocess, "run", side_effect=fake_run):
            rc, out = self.run_main("--apply")
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL: libxcrypt install rc=1", out)

    def test_json_output_carries_the_sets(self):
        self.add_recipe("core", "zstd", "1.5.7", 2)
        make_archive(self.archives, "zstd", "1.5.7", 2)
        out_path = Path(self._tmp.name) / "report.json"
        rc, _ = self.run_main("--json", str(out_path))
        data = json.loads(out_path.read_text())
        self.assertEqual([e["name"] for e in data["redeploy"]], ["zstd"])
        self.assertEqual(data["rebuild"], [])


if __name__ == "__main__":
    unittest.main()
