"""preflight-bash-tier-currency: the bash tiers' missing skip-built layer.

Covers: coverage-vs-refusal against --start-at, the never-built new package,
kernel special-case, ships_as identity, driver-map derivation from real
run_package lines, the from-scratch self-skip, and setup-error exits.
"""

import importlib.util
import io
import json
import os
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
        self.memo_dir = base / "memo"
        patcher = mock.patch.object(self.mod._state, "MEMO_DIR", self.memo_dir, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

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


    def memo_archive(self):
        self.add_recipe("core", "zlib", "1.3.1", 2)
        return make_archive(self.archives, "zlib", "1.3.1", 2)

    def memo_path(self):
        return self.memo_dir / "bash-tier-headers.json"

    def test_memo_unchanged_preserves_pass_and_counts(self):
        self.memo_archive()
        rc, first = self.run_main("--start-at", "desktop")
        self.assertEqual(rc, 0, first)
        self.assertIn("currency: 1 archives read, 0 memoized (unchanged)", first)
        with mock.patch.object(self.mod._state, "read_pkginfo", side_effect=AssertionError("unexpected read")):
            rc, second = self.run_main("--start-at", "desktop")
        self.assertEqual(rc, 0, second)
        self.assertIn("currency: 0 archives read, 1 memoized (unchanged)", second)
        self.assertEqual([x for x in first.splitlines() if "currency:" not in x],
                         [x for x in second.splitlines() if "currency:" not in x])

    def test_memo_stale_verdict_recomputed(self):
        self.memo_archive()
        self.run_main("--start-at", "desktop")
        recipe = self.packages / "core/zlib/package.yml"
        recipe.write_text(recipe.read_text().replace("release: 2", "release: 3"))
        rc, out = self.run_main("--start-at", "desktop")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: zlib", out)
        self.assertIn("0 archives read, 1 memoized", out)

    def test_memo_rewritten_archive_same_path(self):
        archive = self.memo_archive()
        self.run_main("--start-at", "desktop")
        replacement = make_archive(self.archives, "zlib", "1.3.1", 1)
        replacement.replace(archive)
        rc, out = self.run_main("--start-at", "desktop")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: zlib", out)
        self.assertIn("1 archives read, 0 memoized", out)

    def test_memo_nanosecond_mtime_change(self):
        archive = self.memo_archive()
        self.run_main()
        st = archive.stat()
        os.utime(archive, ns=(st.st_atime_ns, st.st_mtime_ns + 1))
        _, out = self.run_main()
        self.assertIn("1 archives read, 0 memoized", out)

    def test_memo_same_size_rewrite_with_restored_mtime(self):
        archive = self.memo_archive()
        self.run_main()
        st = archive.stat()
        archive.write_bytes(b"!" + archive.read_bytes()[1:])
        os.utime(archive, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(archive.stat().st_size, st.st_size)
        self.assertEqual(archive.stat().st_mtime_ns, st.st_mtime_ns)
        rc, out = self.run_main("--start-at", "desktop")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: zlib", out)
        self.assertIn("1 archives read, 0 memoized", out)

    def test_memo_changed_during_read_not_saved(self):
        archive = self.memo_archive()
        read = self.mod._state.read_pkginfo
        def changing(path):
            info = read(path)
            st = path.stat()
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1))
            return info
        with mock.patch.object(self.mod._state, "read_pkginfo", changing):
            rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertIn("archive changed during read", out)
        self.assertEqual(json.loads(self.memo_path().read_text())["entries"], {})

    def test_memo_new_and_removed_archive(self):
        original = self.memo_archive()
        self.run_main()
        newer = make_archive(self.archives, "zlib", "1.3.1", 3)
        _, out = self.run_main()
        self.assertIn("1 archives read, 1 memoized", out)
        newer.unlink()
        rc, out = self.run_main("--start-at", "desktop")
        self.assertEqual(rc, 0, out)
        self.assertIn("0 archives read, 1 memoized", out)
        memo = json.loads(self.memo_path().read_text())
        self.assertEqual(set(memo["entries"]), {str(original.resolve())})

    def test_memo_reader_hash_change(self):
        self.memo_archive()
        self.run_main()
        with mock.patch.object(self.mod._state, "reader_hash", return_value="f" * 64):
            _, out = self.run_main()
        self.assertIn("reader hash changed", out)
        self.assertIn("1 archives read, 0 memoized", out)

    def test_memo_no_memo_rewrites(self):
        self.memo_archive()
        self.run_main()
        rc, out = self.run_main("--no-memo")
        self.assertEqual(rc, 0, out)
        self.assertIn("1 archives read, 0 memoized", out)
        _, out = self.run_main()
        self.assertIn("0 archives read, 1 memoized", out)

    def test_memo_invalid_json_rebuilds(self):
        self.memo_archive()
        self.run_main()
        self.memo_path().write_text('{"entries":')
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertIn("memo ignored", out)
        self.assertIn("1 archives read, 0 memoized", out)
        json.loads(self.memo_path().read_text())

    def test_memo_invalid_payload_rebuilds(self):
        self.memo_archive()
        self.run_main()
        memo = json.loads(self.memo_path().read_text())
        next(iter(memo["entries"].values()))["value"] = ["not a header"]
        self.memo_path().write_text(json.dumps(memo))
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertIn("memo ignored", out)
        self.assertIn("1 archives read, 0 memoized", out)

    def test_memo_unreadable_file_rebuilds(self):
        self.memo_archive()
        self.run_main()
        read_text = Path.read_text
        def read(path, *args, **kwargs):
            if path == self.memo_path():
                raise PermissionError("fixture memo unreadable")
            return read_text(path, *args, **kwargs)
        with mock.patch.object(Path, "read_text", read):
            rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertIn("memo ignored", out)
        self.assertIn("PermissionError", out)
        self.assertIn("1 archives read, 0 memoized", out)

    def test_memo_write_failure_keeps_verdict(self):
        self.memo_archive()
        self.memo_dir.write_text("not a directory")
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertIn("memo not written", out)
        self.assertIn("1 archives read, 0 memoized", out)

    def test_memo_strict_reader_rejects_corrupt_archive(self):
        archive = self.memo_archive()
        self.run_main()
        archive.write_bytes(b"not a tar archive")
        with self.assertRaisesRegex(ValueError, "cannot read package identity"):
            self.mod._state.newest_archives(self.archives, strict=True)

    def test_memo_different_store_does_not_reuse_header(self):
        self.memo_archive()
        self.run_main()
        other = self.chroot / "other"
        other.mkdir()
        make_archive(other, "zlib", "1.3.1", 1)
        with mock.patch.object(self.mod._state, "read_pkginfo", wraps=self.mod._state.read_pkginfo) as reader:
            newest = self.mod._state.newest_archives(other)
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(newest["zlib"][:2], ("1.3.1", 1))


if __name__ == "__main__":
    unittest.main()
