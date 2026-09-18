"""A staging build never opens the running system's package database.

The builder's post_install step brackets the recipe's hook with a baseline of
the package's own file hashes and a comparison afterwards, so a file the hook
rewrites in place is recorded as hook-managed instead of reported as damage.
Both halves read and write the package's row in the live package database.

A --stage-only build registers no row. There is nothing to baseline and
nothing to compare, yet the pair ran anyway: measured on an installed machine,
a stage-only build logged

    warning: pkm DB open failed for hook baseline: attempt to write a
    readonly database

The write failed and the machine was untouched, but the reach is the defect. A
staging build must not open the running system's package database at all, and a
run that is only prevented by the process being unprivileged is not prevented.

The double below replaces the database class the tracker module binds at
import. It RAISES, so the test cannot pass by the constructor quietly doing
nothing, and it counts its calls, so "never opened" is a measured zero rather
than the absence of a log line.
"""

from __future__ import annotations

import hashlib
import importlib
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from .factories import make_package, make_source  # noqa: E402

_builder = importlib.import_module("igos-build.builder")
_tracker = importlib.import_module("igos-build.tracker")
BuildExecutor = _builder.BuildExecutor

BUILD_SH = """\
do_install() {
    set -e
    install -d "$DESTDIR/usr/share/demo"
    echo installed > "$DESTDIR/usr/share/demo/marker"
}

post_install() {
    set -e
    echo "the hook ran"
}
"""


class _CountingRefusal:
    """Stands in for the package database class. Never opens anything."""

    calls = 0

    def __init__(self, *args, **kwargs):
        type(self).calls += 1
        raise RuntimeError(
            "a staging build opened the running system's package database")


class StageOnlyNeverOpensThePackageDatabase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.recipe_dir = self.tmp / "recipe"
        self.recipe_dir.mkdir()
        (self.recipe_dir / "build.sh").write_text(BUILD_SH)

        # A real source tarball, so the build runs its real phases.
        src_tree = self.tmp / "demo-1.0"
        src_tree.mkdir()
        (src_tree / "README").write_text("demo\n")
        self.sources = self.tmp / "sources"
        self.sources.mkdir()
        tarball = self.sources / "demo-1.0.tar.gz"
        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(src_tree, arcname="demo-1.0")
        digest = hashlib.sha256(tarball.read_bytes()).hexdigest()

        self.pkg = make_package(
            name="demo", version="1.0", tier="core",
            build_style="custom", install_func="do_install",
            template_path=self.recipe_dir / "package.yml",
            source=[make_source(
                url="https://example.invalid/demo-1.0.tar.gz",
                sha256=digest)],
        )

        self._real_db = _tracker.PackageDB
        _CountingRefusal.calls = 0
        _tracker.PackageDB = _CountingRefusal
        self.addCleanup(setattr, _tracker, "PackageDB", self._real_db)
        self.addCleanup(self._tmp.cleanup)

    def _executor(self, tracked: bool) -> "BuildExecutor":
        # Built untracked in every case so the constructor never creates the
        # absolute tracking directories on the machine running the test; the
        # tracked leg sets the flag and redirects those paths into the
        # temporary tree afterwards, which is what the guard under test reads.
        ex = BuildExecutor(
            work_dir=self.tmp / "work",
            log_dir=self.tmp / "logs",
            sources_dir=self.sources,
            patches_dir=self.tmp / "patches",
            system_root=self.tmp / "system",
        )
        if tracked:
            ex.tracked = True
            ex.pkg_db = self.tmp / "pkgdb"
            ex.pkg_archives = self.tmp / "archives"
            ex.pkg_staging = self.tmp / "staging"
            for d in (ex.pkg_db, ex.pkg_archives, ex.pkg_staging):
                d.mkdir(parents=True, exist_ok=True)
        return ex

    def _log_text(self) -> str:
        return "\n".join(
            p.read_text(errors="replace")
            for p in (self.tmp / "logs").rglob("*") if p.is_file())

    def test_a_stage_only_build_never_constructs_the_database(self):
        ok = self._executor(tracked=False).build_package(self.pkg)
        self.assertTrue(ok, self._log_text()[-3000:])
        self.assertEqual(
            _CountingRefusal.calls, 0,
            "a stage-only build reached for the package database")

    def test_a_stage_only_build_logs_no_database_failure(self):
        self._executor(tracked=False).build_package(self.pkg)
        self.assertNotIn("DB open failed", self._log_text())

    def test_the_hook_still_runs_in_a_stage_only_build(self):
        ok = self._executor(tracked=False).build_package(self.pkg)
        self.assertTrue(ok)
        self.assertIn("the hook ran", self._log_text())

    def test_the_baseline_step_itself_still_opens_the_database(self):
        """The tracked half is untouched, and the double really does bite.

        A tracked build cannot be driven to its post_install step in an
        unprivileged test: the track phase changes ownership inside the
        staging tree and stops at PermissionError long before the hook. So
        the tracked half is pinned where it lives — the baseline method the
        guard calls when self.tracked is true — which also proves this
        test's double is reached at all, rather than a zero above meaning
        the substitution silently failed.
        """
        ex = self._executor(tracked=False)
        ex.tracked = True
        self.assertEqual(ex.pkg_hook_baseline(self.pkg), {})
        self.assertGreater(
            _CountingRefusal.calls, 0,
            "the baseline step no longer opens the package database")


if __name__ == "__main__":
    unittest.main()
