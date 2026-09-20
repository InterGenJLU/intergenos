"""A files/ overlay is deployed root-owned, or the build refuses and says why.

`overlay_package_files` copies a package's `files/` tree into DESTDIR with
`cp -an` and then chowns every copied path to root:root. That chown is a
security control: `cp -a` preserves the REPO checkout's owner — the build
user's uid — and shipping that ownership leaves /etc/passwd and /etc/group
writable by the first human user on the installed system.

An unprivileged builder cannot perform that chown. Today it raises
PermissionError out of the middle of the copy, so the build dies with a
traceback after DESTDIR has already been populated, and the message says
nothing about what the builder needed or how to give it to it. These tests
pin the honest behaviour: refuse BEFORE copying, name the cause and the
remedy, and never leave a half-deployed overlay behind. Silently skipping the
chown is the one answer the tests forbid — an overlay that ships with the
build user's uid is the defect the chown exists to prevent.
"""

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
_builder_mod = importlib.import_module("igos-build.builder")
BuildExecutor = _builder_mod.BuildExecutor


class _Logger:
    """Records what the builder said, so a test can assert the reason."""

    def __init__(self):
        self.errors = []
        self.infos = []
        self.started = False

    def error(self, msg):
        self.errors.append(str(msg))

    def start_package(self, *args, **kwargs):
        self.started = True

    def info(self, msg):
        self.infos.append(str(msg))

    def warning(self, msg):
        self.infos.append(str(msg))


class OverlayOwnershipTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.pkg_dir = root / "packages" / "desktop" / "demo"
        self.files_dir = self.pkg_dir / "files"
        (self.files_dir / "usr" / "lib" / "systemd" / "system").mkdir(parents=True)
        (self.files_dir / "usr" / "lib" / "systemd" / "system" / "demo.service"
         ).write_text("[Unit]\nDescription=demo\n")
        self.destdir = root / "destdir"
        self.destdir.mkdir()
        self.logger = _Logger()
        self.ex = BuildExecutor.__new__(BuildExecutor)
        self.ex.logger = self.logger
        self.pkg = SimpleNamespace(
            name="demo", template_path=self.pkg_dir / "package.yml")
        self.env = {"DESTDIR": str(self.destdir)}

    def tearDown(self):
        self._tmp.cleanup()

    def _deployed(self):
        return sorted(p.relative_to(self.destdir).as_posix()
                      for p in self.destdir.rglob("*") if p.is_file())

    def test_an_unprivileged_build_refuses_and_copies_nothing(self):
        with mock.patch.object(os, "geteuid", return_value=1000):
            ok = self.ex.overlay_package_files(self.pkg, self.env)
        self.assertFalse(
            ok, "an overlay that cannot be made root-owned must fail the build")
        self.assertEqual(
            self._deployed(), [],
            "the refusal must come before the copy, not after it")

    def test_the_refusal_names_the_cause_and_the_remedy(self):
        with mock.patch.object(os, "geteuid", return_value=1000):
            self.ex.overlay_package_files(self.pkg, self.env)
        said = "\n".join(self.logger.errors)
        self.assertIn("overlay-files", said)
        self.assertIn("root", said.lower())
        self.assertIn("unshare -r", said,
                      "the message must name the remedy the fleet uses")

    def test_a_privileged_build_deploys_and_owns_every_path_root(self):
        chowned = []

        def fake_chown(path, uid, gid, follow_symlinks=True):
            chowned.append((str(path), uid, gid))

        with mock.patch.object(os, "geteuid", return_value=0), \
                mock.patch.object(os, "chown", side_effect=fake_chown):
            ok = self.ex.overlay_package_files(self.pkg, self.env)
        self.assertTrue(ok)
        self.assertIn("usr/lib/systemd/system/demo.service", self._deployed())
        self.assertTrue(chowned, "every deployed path is chowned to root:root")
        for _, uid, gid in chowned:
            self.assertEqual((uid, gid), (0, 0))

    def test_a_chown_refused_at_run_time_fails_the_build_with_a_reason(self):
        # Privileged by euid but without the capability — a container without
        # CAP_CHOWN, or a filesystem that refuses it. The builder must report,
        # not raise.
        def refusing_chown(path, uid, gid, follow_symlinks=True):
            raise PermissionError(1, "Operation not permitted", str(path))

        with mock.patch.object(os, "geteuid", return_value=0), \
                mock.patch.object(os, "chown", side_effect=refusing_chown):
            ok = self.ex.overlay_package_files(self.pkg, self.env)
        self.assertFalse(ok)
        said = "\n".join(self.logger.errors)
        self.assertIn("overlay-files", said)
        self.assertIn("root", said.lower())

    def test_the_refusal_comes_before_the_compile(self):
        # build_package must stop at its own pre-flight: the work directory
        # for the package is never even created, so no source is fetched and
        # nothing is compiled before the builder says it cannot finish.
        work_dir = Path(self._tmp.name) / "work"
        work_dir.mkdir()
        self.ex.work_dir = work_dir
        pkg = SimpleNamespace(
            name="demo", version="1.0", build_style="custom",
            template_path=self.pkg_dir / "package.yml")
        with mock.patch.object(os, "geteuid", return_value=1000):
            ok = self.ex.build_package(pkg)
        self.assertFalse(ok)
        self.assertFalse((work_dir / "demo").exists(),
                         "no work was started for a package that cannot finish")
        said = "\n".join(self.logger.errors)
        self.assertIn("unshare -r", said)
        self.assertIn("root", said.lower())

    def test_the_overlay_helper_names_only_packages_that_have_one(self):
        self.assertIsNotNone(BuildExecutor.overlay_files_dir(self.pkg))
        nofiles = SimpleNamespace(
            name="nofiles",
            template_path=self.pkg_dir.parent / "nofiles" / "package.yml")
        self.assertIsNone(BuildExecutor.overlay_files_dir(nofiles))
        self.assertIsNone(BuildExecutor.overlay_files_dir(
            SimpleNamespace(name="notemplate", template_path=None)))

    def test_a_package_with_no_files_overlay_is_unaffected(self):
        pkg = SimpleNamespace(
            name="nofiles",
            template_path=self.pkg_dir.parent / "nofiles" / "package.yml")
        with mock.patch.object(os, "geteuid", return_value=1000):
            self.assertTrue(self.ex.overlay_package_files(pkg, self.env))


if __name__ == "__main__":
    unittest.main()
