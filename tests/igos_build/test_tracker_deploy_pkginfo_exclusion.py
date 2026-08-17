"""Regression tests: pkg_deploy ships the payload only, never .PKGINFO.

pkg_manifest writes ./.PKGINFO into the staging tree AFTER manifest
enumeration so pkg_archive packs a self-describing tarball for the repo
index. pkg_deploy then tarred the WHOLE staging dir onto the live root, so
every staged package deployed an untracked, unverified /.PKGINFO that the
next package silently overwrote — metadata leaking into the payload. The
deploy pipeline now excludes ./.PKGINFO; the archive keeps carrying it.
"""

import importlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace

# Import the LIVE module from the worktree this test file lives in — never a
# hardcoded absolute root (a hardcoded root silently tests the wrong tree
# when running from a second worktree).
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from .factories import make_package  # noqa: E402
_tracker_mod = importlib.import_module("igos-build.tracker")
PackageTracker = _tracker_mod.PackageTracker


class _Logger:
    def __init__(self):
        self.errors, self.warnings, self.infos = [], [], []

    def error(self, msg):
        self.errors.append(msg)

    def warning(self, msg):
        self.warnings.append(msg)

    def info(self, msg):
        self.infos.append(msg)


def _mk_stub(tmp: Path):
    stub = SimpleNamespace(logger=_Logger(), pkg_archives=tmp / "archives")
    stub.pkg_archives.mkdir(parents=True, exist_ok=True)
    # Bind the real B4 validator so the deploy path under test is the
    # production gate chain, not a shortcut.
    stub._validate_staging_paths = MethodType(
        PackageTracker._validate_staging_paths, stub
    )
    return stub


def _mk_staging(tmp: Path, name="foo", version="1.0"):
    staging = tmp / "staging" / f"{name}-{version}"
    (staging / "usr" / "bin").mkdir(parents=True)
    tool = staging / "usr" / "bin" / name
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o4755)  # setuid — exercises the restore pass too
    (staging / ".PKGINFO").write_text("pkgname = foo\npkgver = 1.0\n")
    return staging


class TestDeployExcludesPkginfo(unittest.TestCase):
    def test_payload_deploys_pkginfo_does_not(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = tmp / "liveroot"
            root.mkdir()
            staging = _mk_staging(tmp)
            stub = _mk_stub(tmp)
            pkg = make_package(name="foo")

            ok = PackageTracker.pkg_deploy(stub, pkg, staging, root=str(root))

            self.assertTrue(ok, f"deploy failed: {stub.logger.errors}")
            deployed_tool = root / "usr" / "bin" / "foo"
            self.assertTrue(deployed_tool.exists(), "payload must deploy")
            self.assertFalse(
                (root / ".PKGINFO").exists(),
                ".PKGINFO is archive metadata and must NOT reach the live root",
            )

    def test_setuid_survives_deploy(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = tmp / "liveroot"
            root.mkdir()
            staging = _mk_staging(tmp, name="bar")
            stub = _mk_stub(tmp)
            pkg = make_package(name="bar")

            ok = PackageTracker.pkg_deploy(stub, pkg, staging, root=str(root))

            self.assertTrue(ok, f"deploy failed: {stub.logger.errors}")
            mode = os.stat(root / "usr" / "bin" / "bar").st_mode
            self.assertTrue(
                mode & stat.S_ISUID,
                "setuid bit must survive deploy (restore pass)",
            )

    def test_bash_sibling_excludes_pkginfo(self):
        # The bash core/base deploy path (scripts/pkg-functions.sh) tars the
        # same staging layout to / — assert its pipeline carries the same
        # exclusion so the class cannot regress on one side only.
        sh = (REPO_ROOT / "scripts" / "pkg-functions.sh").read_text()
        self.assertIn(
            "--exclude='./.PKGINFO'",
            sh,
            "bash pkg_deploy must exclude ./.PKGINFO like the Python tracker",
        )


if __name__ == "__main__":
    unittest.main()
