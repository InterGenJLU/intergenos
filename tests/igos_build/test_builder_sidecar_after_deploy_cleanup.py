"""Regression tests for the staged-package verify_paths sidecar derivation.

pkg_deploy removes the staging tree when a deploy succeeds, and the
builder's success branch derives the auto-verify-paths sidecar AFTER the
register gate — so the old staging-directory walk always hit the
missing-dir guard and the sidecar silently never derived for staged
packages. The derivation now prefers the file list pkg_register_pkm_db
recorded (the only surviving source), keeping the staging walk as a
fallback for pre-cleanup callers.
"""

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

# Import the LIVE module from the worktree this test file lives in — never a
# hardcoded absolute root (a hardcoded root silently tests the wrong tree
# when running from a second worktree).
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from .factories import make_package  # noqa: E402
_builder_mod = importlib.import_module("igos-build.builder")
BuildExecutor = _builder_mod.BuildExecutor


class _Logger:
    def __init__(self):
        self.infos = []

    def info(self, msg):
        self.infos.append(msg)


def _mk_pkg(root: Path, name="foo"):
    d = root / "packages" / "core" / name
    d.mkdir(parents=True)
    (d / "package.yml").write_text(f"name: {name}\n")
    return make_package(name=name, template_path=d / "package.yml")


class TestSidecarAfterDeployCleanup(unittest.TestCase):
    def test_derives_from_registered_paths_when_staging_gone(self):
        # The success-branch reality: staging was rmtree'd by pkg_deploy,
        # but the register gate recorded the file list.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = _mk_pkg(root)
            stub = SimpleNamespace(
                logger=_Logger(),
                _last_registered_paths=["usr/bin/foo", "usr/lib/libfoo.so"],
            )
            gone = root / "staging" / "foo-1.0"  # never created
            BuildExecutor._auto_derive_verify_paths_from_staging(stub, pkg, gone)
            sidecar = pkg.template_path.parent / "auto-verify-paths.json"
            self.assertTrue(
                sidecar.exists(),
                "sidecar must derive from the registered file list even "
                "though staging is already cleaned up",
            )
            derived = json.loads(sidecar.read_text())
            self.assertTrue(derived, "sidecar must contain derived paths")

    def test_falls_back_to_staging_walk_pre_cleanup(self):
        # A caller running before deploy cleanup (no recorded list yet)
        # still derives from the staging tree.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = _mk_pkg(root, name="bar")
            staging = root / "staging" / "bar-1.0"
            (staging / "usr" / "bin").mkdir(parents=True)
            (staging / "usr" / "bin" / "bar").write_text("#!/bin/sh\n")
            stub = SimpleNamespace(logger=_Logger())
            BuildExecutor._auto_derive_verify_paths_from_staging(stub, pkg, staging)
            sidecar = pkg.template_path.parent / "auto-verify-paths.json"
            self.assertTrue(sidecar.exists())

    def test_no_list_and_no_staging_derives_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = _mk_pkg(root, name="baz")
            stub = SimpleNamespace(logger=_Logger())
            BuildExecutor._auto_derive_verify_paths_from_staging(
                stub, pkg, root / "absent"
            )
            sidecar = pkg.template_path.parent / "auto-verify-paths.json"
            self.assertFalse(sidecar.exists())


if __name__ == "__main__":
    unittest.main()
