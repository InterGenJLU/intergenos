"""Regression tests: supersedee overlap is measured, never assumed.

The old scheme excluded the supersedee's tracked paths from the PRE-build
snapshot, so every surviving predecessor file the build never touched
appeared in after-minus-before and the successor's manifest/archive/DB
claimed payload it never wrote (the mtime overwrite filter was defeated by
the union). Supersedee paths now stay in both snapshots: net-new is only
what the build genuinely created, and overlap is added solely by measured
mtime detection.
"""

import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace

# Import the LIVE modules from the worktree this test file lives in — never a
# hardcoded absolute root (a hardcoded root silently tests the wrong tree
# when running from a second worktree).
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from .factories import make_package, make_tracker_stub  # noqa: E402
_tracker_mod = importlib.import_module("igos-build.tracker")
_builder_mod = importlib.import_module("igos-build.builder")
PackageTracker = _tracker_mod.PackageTracker
BuildExecutor = _builder_mod.BuildExecutor


class _Logger:
    def __init__(self):
        self.errors, self.infos = [], []

    def error(self, msg):
        self.errors.append(msg)

    def info(self, msg):
        self.infos.append(msg)


def _mk_stub(tmp: Path):
    # Binds the whole class — see make_tracker_stub for why a hand-written
    # method list is drift by construction.
    stub = make_tracker_stub(logger=_Logger(), pkg_db=tmp / "pkg_db")
    stub.pkg_db.mkdir(parents=True, exist_ok=True)
    return stub


def _write_supersedee_manifest(pkg_db: Path, name: str, paths):
    lines = "\n".join(p.lstrip("/") for p in paths)
    (pkg_db / f"{name}-1.0").write_text(
        f"PACKAGE NAME: {name}-1.0\n"
        f"PACKAGE VERSION: 1.0\n"
        f"DESCRIPTION:\n{name}: predecessor\n\nFILE LIST:\n{lines}\n"
    )


def _mk_pkg(tmp: Path, name="kernel-new", supersedes=("kernel-old",)):
    d = tmp / "packages" / "core" / name
    d.mkdir(parents=True)
    (d / "package.yml").write_text(f"name: {name}\n")
    return make_package(
        name=name, version="2.0", description="successor",
        supersedes=list(supersedes), template_path=d / "package.yml",
    )


class TestSupersedeeOverlapMeasured(unittest.TestCase):
    def _run_diff(self, tmp, touched_survivor: bool):
        """Build the fixture and run pkg_manifest_from_diff; return manifest.

        review finding H3: before/after are metadata snapshots (path -> size, mtime_ns,
        ctime_ns). The rewritten-survivor case is caught by the ctime delta
        (generalizing the old supersedee-only mtime window).
        """
        stub = _mk_stub(tmp)
        # The surviving predecessor file — on disk before AND after the build.
        survivor = tmp / "usr" / "lib" / "modules" / "old" / "mod.ko"
        survivor.parent.mkdir(parents=True)
        survivor.write_text("predecessor payload\n")
        # The genuinely new file this build creates.
        newfile = tmp / "usr" / "lib" / "modules" / "new" / "mod.ko"
        newfile.parent.mkdir(parents=True)
        newfile.write_text("successor payload\n")

        _write_supersedee_manifest(stub.pkg_db, "kernel-old", [str(survivor)])
        pkg = _mk_pkg(tmp)

        build_start = time.time()
        if not touched_survivor:
            # Predecessor predates the build window: old mtime so the retained
            # supersedee mtime check does not flag it. The pre-build snapshot is
            # taken AFTER this so the survivor's ctime matches the after snapshot
            # (an untouched file has no ctime delta).
            os.utime(survivor, (build_start - 3600, build_start - 3600))

        def _snap(p):
            st = os.lstat(p)
            return (st.st_size, st.st_mtime_ns, st.st_ctime_ns)

        # Unfiltered snapshots: the survivor is in BOTH; only newfile is new.
        before = {str(survivor): _snap(survivor)}
        if touched_survivor:
            time.sleep(0.01)
            survivor.write_text("rewritten by this build\n")  # bumps ctime
        after = {str(survivor): _snap(survivor), str(newfile): _snap(newfile)}

        ok = PackageTracker.pkg_manifest_from_diff(
            stub, pkg, before, after, build_start_time=build_start - 1
        )
        self.assertTrue(ok, f"manifest_from_diff failed: {stub.logger.errors}")
        manifest = (stub.pkg_db / "kernel-new-2.0").read_text()
        return manifest

    def test_untouched_survivor_stays_with_predecessor(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = self._run_diff(Path(td), touched_survivor=False)
            self.assertIn("modules/new/mod.ko", manifest,
                          "genuinely-new payload must be claimed")
            self.assertNotIn(
                "modules/old/mod.ko", manifest,
                "an untouched surviving predecessor file must NOT be "
                "claimed by the successor",
            )

    def test_rewritten_survivor_is_claimed_as_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = self._run_diff(Path(td), touched_survivor=True)
            self.assertIn("modules/new/mod.ko", manifest)
            self.assertIn(
                "modules/old/mod.ko", manifest,
                "a predecessor path the build genuinely rewrote (ctime delta) "
                "must transfer to the successor",
            )

    def test_builder_snapshot_is_unfiltered(self):
        # The builder must not exclude supersedee paths from the pre-build
        # snapshot — source-level guard against reintroducing the exclusion.
        import inspect
        src = inspect.getsource(BuildExecutor.build_package)
        self.assertNotIn(
            "exclude_paths=", src,
            "build_package must take an UNFILTERED pre-build snapshot",
        )


if __name__ == "__main__":
    unittest.main()
