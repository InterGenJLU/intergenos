"""preflight-bash-tier-currency: the bash tiers' missing skip-built layer.

Covers: coverage-vs-refusal against --start-at, the never-built new package,
kernel special-case, ships_as identity, driver-map derivation from real
run_package lines, the from-scratch self-skip, and setup-error exits.
"""

import importlib.util
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Reuse the sibling suite's archive fixture builder.
_fx_spec = importlib.util.spec_from_file_location(
    "redeploy_fixtures", Path(__file__).parent / "test_redeploy_banked_archives.py")
_fx = importlib.util.module_from_spec(_fx_spec)
_fx_spec.loader.exec_module(_fx)
make_archive = _fx.make_archive


def _load():
    spec = importlib.util.spec_from_file_location(
        "bash_tier_currency_test", REPO_ROOT / "scripts" / "preflight-bash-tier-currency.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BashTierCurrencyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="btc-test.")
        base = Path(self._tmp.name)
        self.chroot = base / "chroot"
        self.archives = self.chroot / "var/lib/igos/archives"
        self.archives.mkdir(parents=True)
        self.packages = base / "packages"
        self.packages.mkdir()
        self.scripts = base / "scripts"
        self.scripts.mkdir()
        (self.scripts / "chroot-build-ch8.sh").write_text(
            'run_package zlib\nrun_package glibc\n')
        (self.scripts / "chroot-build-core-extra.sh").write_text(
            'run_package linux-kernel-pass2\nrun_package sqlite\n')
        (self.scripts / "chroot-build-base.sh").write_text(
            'run_package htop\n')
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

    def run_main(self, *extra):
        argv = ["preflight-bash-tier-currency.py",
                "--chroot", str(self.chroot),
                "--packages-dir", str(self.packages),
                "--scripts-dir", str(self.scripts), *extra]
        buf = io.StringIO()
        with mock.patch("sys.argv", argv), redirect_stdout(buf):
            rc = self.mod.main()
        return rc, buf.getvalue()

    def test_empty_chroot_self_skips_clean(self):
        self.add_recipe("core", "zlib", "1.3.1")
        rc, out = self.run_main("--start-at", "desktop")
        self.assertEqual(rc, 0, out)
        self.assertIn("SKIP (clean)", out)

    def test_current_archive_passes(self):
        self.add_recipe("core", "zlib", "1.3.1", 2)
        make_archive(self.archives, "zlib", "1.3.1", 2)
        rc, out = self.run_main("--start-at", "desktop")
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS", out)

    def test_stale_uncovered_refuses_with_names(self):
        self.add_recipe("core", "zlib", "1.3.1", 3)
        make_archive(self.archives, "zlib", "1.3.1", 2)
        rc, out = self.run_main("--start-at", "desktop")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: zlib", out)
        self.assertIn("--start-at core or earlier", out)

    def test_stale_covered_by_start_at_passes_named(self):
        self.add_recipe("core", "zlib", "1.3.1", 3)
        make_archive(self.archives, "zlib", "1.3.1", 2)
        # Another package keeps the chroot non-empty and current.
        self.add_recipe("core", "glibc", "2.41", 1)
        make_archive(self.archives, "glibc", "2.41", 1)
        rc, out = self.run_main("--start-at", "core")
        self.assertEqual(rc, 0, out)
        self.assertIn("WILL REBUILD on this resume: zlib", out)

    def test_full_build_covers_everything(self):
        self.add_recipe("core", "zlib", "1.3.1", 3)
        make_archive(self.archives, "zlib", "1.3.1", 2)
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertIn("WILL REBUILD", out)

    def test_never_built_new_package_uncovered_refuses(self):
        self.add_recipe("base", "htop", "3.4.0")
        self.add_recipe("core", "zlib", "1.3.1", 1)
        make_archive(self.archives, "zlib", "1.3.1", 1)
        rc, out = self.run_main("--start-at", "kernel")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: htop [NEVER-BUILT]", out)

    def test_core_extra_phase_mapping_from_driver(self):
        self.add_recipe("core", "sqlite", "3.50.0", 2)
        make_archive(self.archives, "sqlite", "3.50.0", 1)
        rc, out = self.run_main("--start-at", "core-extra")
        self.assertEqual(rc, 0, out)
        self.assertIn("phase 'core-extra'", out)

    def test_kernel_special_case_refuses_after_kernel_phase(self):
        self.add_recipe("core", "linux-kernel", "6.18.10", 10)
        make_archive(self.archives, "linux-kernel", "6.18.10", 9)
        rc, out = self.run_main("--start-at", "desktop")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: linux-kernel", out)
        self.assertIn("phase 'kernel'", out)

    def test_ships_as_twin_currency_via_ship_identity(self):
        self.add_recipe("core", "glibc-pass2", "2.41", 2, ships_as="glibc")
        make_archive(self.archives, "glibc", "2.41", 1)
        # Driver builds the recipe under its dir name.
        (self.scripts / "chroot-build-ch8.sh").write_text('run_package glibc-pass2\n')
        rc, out = self.run_main("--start-at", "desktop")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: glibc", out)

    def test_unknown_start_at_is_setup_error(self):
        rc, out = self.run_main("--start-at", "nonsense")
        self.assertEqual(rc, 2, out)
        self.assertIn("SETUP ERROR", out)

    def test_unwired_bash_package_left_to_tier_coverage_gate(self):
        self.add_recipe("core", "orphan-pkg", "1.0", 5)
        self.add_recipe("core", "zlib", "1.3.1", 1)
        make_archive(self.archives, "zlib", "1.3.1", 1)
        rc, out = self.run_main("--start-at", "desktop")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("orphan-pkg", out)


if __name__ == "__main__":
    unittest.main()
