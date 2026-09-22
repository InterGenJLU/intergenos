# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A built archive is named for the build it is, release included.

Two releases of one version are different bytes with different contents, and
until now every path that CREATED an archive called both of them
``<name>-<version>.igos.tar.gz``. pkm's download cache had already moved to
``<name>-<version>-<release>.igos.tar.gz`` precisely so that the release a
machine is upgrading away from survives long enough to be rolled back to, and
the producers never followed. An archive that cannot name its release cannot
be told apart from the build it replaces.

These cases run the REAL producers — the tracker's archive step against a real
staging directory, the shell ``pkg_archive`` function extracted from
``scripts/pkg-functions.sh`` and run as a real bash process, the builder's
quarantine step, and ``scripts/emit-package-archives.py`` against a scratch
manifest — and read the resulting filename off disk.
"""

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from .factories import make_package, make_tracker_stub  # noqa: E402

_builder_mod = importlib.import_module("igos-build.builder")
BuildExecutor = _builder_mod.BuildExecutor


class _CapturingLogger:
    def __init__(self):
        self.errors = []
        self.infos = []

    def error(self, msg):
        self.errors.append(msg)

    def info(self, msg):
        self.infos.append(msg)


class TestTheTrackerNamesTheArchiveForItsRelease(unittest.TestCase):
    """The DESTDIR path, which is what a tracked build actually runs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.archives = self.tmp / "archives"
        self.archives.mkdir()
        self.staging = self.tmp / "staging" / "demo-1.0"
        (self.staging / "usr/bin").mkdir(parents=True)
        (self.staging / "usr/bin/demo").write_text("#!/bin/sh\nexit 0\n")
        self.logger = _CapturingLogger()

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, release):
        pkg = make_package(name="demo", version="1.0", release=release)
        stub = make_tracker_stub(
            pkg_archives=self.archives,
            logger=self.logger,
            _enforce_mirror_archive_verify_paths=lambda *a, **k: True,
        )
        ok = stub.pkg_archive(pkg, self.staging)
        self.assertTrue(ok, f"pkg_archive refused: {self.logger.errors}")
        return sorted(p.name for p in self.archives.iterdir())

    def test_the_release_is_in_the_filename(self):
        self.assertEqual(self._run(7), ["demo-1.0-7.igos.tar.gz"])

    def test_two_releases_of_one_version_are_two_files(self):
        self._run(7)
        self._run(8)
        self.assertEqual(
            sorted(p.name for p in self.archives.iterdir()),
            ["demo-1.0-7.igos.tar.gz", "demo-1.0-8.igos.tar.gz"],
            "one release overwrote the other — the release being replaced is "
            "gone and cannot be rolled back to",
        )


class TestTheBuilderQuarantinesTheNameItWrote(unittest.TestCase):
    """A failed tracked build must move the archive it actually sealed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.pkg_db = self.tmp / "packages"
        self.archives = self.tmp / "archives"
        self.pkg_db.mkdir()
        self.archives.mkdir()
        self.logger = _CapturingLogger()
        self.stub = SimpleNamespace(
            pkg_db=self.pkg_db, pkg_archives=self.archives, logger=self.logger)
        self.pkg = make_package(name="demo", version="1.0", release=7)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_release_carrying_archive_is_the_one_quarantined(self):
        sealed = self.archives / "demo-1.0-7.igos.tar.gz"
        sealed.write_bytes(b"sealed bytes\n")
        BuildExecutor._remove_failed_tracking_artifacts(self.stub, self.pkg)
        self.assertFalse(
            sealed.exists(),
            "the failed build's archive stayed in the ship namespace: "
            + "\n".join(self.logger.errors))
        self.assertTrue((self.archives / "demo-1.0-7.igos.tar.gz.failed").exists())


def _shell_function(name: str) -> str:
    text = (REPO_ROOT / "scripts/pkg-functions.sh").read_text()
    match = re.search(r"^%s\(\) \{\n.*?^\}$" % re.escape(name), text, re.M | re.S)
    if not match:
        raise AssertionError(f"{name} missing from scripts/pkg-functions.sh")
    return match.group()


class TestTheShellArchiveStepNamesTheRelease(unittest.TestCase):
    """The bash tier, run as a real bash process.

    It runs in the pre-python bootstrap window — a PATH carrying only the
    coreutils the function needs and no python3 — which is a real, sanctioned
    state of the build and the one that keeps this case to the naming
    behaviour it is pinning.
    """

    TOOLS = ("bash", "tar", "gzip", "du", "stat", "sed", "grep", "awk", "cut",
             "cat", "rm", "mkdir", "printf", "dirname", "basename", "sort")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for tool in self.TOOLS:
            found = shutil.which(tool)
            if found:
                (self.bin / tool).symlink_to(found)
        self.assertIsNone(
            shutil.which("python3", path=str(self.bin)),
            "the scratch PATH must not reach a python3")
        self.staging = self.tmp / "staging" / "demo-1.0"
        (self.staging / "usr/bin").mkdir(parents=True)
        (self.staging / "usr/bin/demo").write_text("#!/bin/sh\nexit 0\n")
        self.archives = self.tmp / "archives"
        self.archives.mkdir()
        (self.tmp / "db").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args):
        setup = (
            "set +e\n"
            f'IGOS_PKG_STAGING="{self.tmp}/staging"\n'
            f'IGOS_PKG_ARCHIVES="{self.archives}"\n'
            f'IGOS_PKG_DB="{self.tmp}/db"\n'
            f'IGOS_LOGS="{self.tmp}/logs"\n'
            'pkg_log() { printf "%s\\n" "$*"; }\n'
            'pkg_error() { printf "%s\\n" "$*" >&2; }\n'
        )
        script = self.tmp / "run.sh"
        script.write_text(
            setup + _shell_function("pkg_archive") + "\n"
            + "pkg_archive " + " ".join(args) + "\nexit $?\n")
        return subprocess.run(
            [shutil.which("bash"), str(script)],
            env={"PATH": str(self.bin)},
            capture_output=True, text=True, timeout=60,
        )

    def test_a_stated_release_is_in_the_filename(self):
        result = self._run("demo", "1.0", "7")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            sorted(p.name for p in self.archives.iterdir()),
            ["demo-1.0-7.igos.tar.gz"], result.stdout + result.stderr)

    def test_an_unstated_release_keeps_the_release_less_name(self):
        # The recipe-less LFS core packages the bash tier builds know no
        # release. Writing "-1" would assert a build number nothing recorded.
        result = self._run("demo", "1.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            sorted(p.name for p in self.archives.iterdir()),
            ["demo-1.0.igos.tar.gz"], result.stdout + result.stderr)


class TestEmitPackageArchivesNamesTheRelease(unittest.TestCase):
    """The manifest-driven emitter, run as a real process."""

    SCRIPT = REPO_ROOT / "scripts/emit-package-archives.py"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.chroot = self.tmp / "chroot"
        (self.chroot / "usr/bin").mkdir(parents=True)
        (self.chroot / "usr/bin/demo").write_text("#!/bin/sh\nexit 0\n")
        self.manifests = self.tmp / "manifests"
        self.manifests.mkdir()
        self.out = self.tmp / "archives"

    def tearDown(self):
        self._tmp.cleanup()

    def _manifest(self, release_line=""):
        path = self.manifests / "demo-1.0"
        path.write_text(
            "PACKAGE NAME: demo-1.0\n"
            "PACKAGE VERSION: 1.0\n"
            + release_line +
            "FILE LIST:\n"
            "usr/bin/demo\n"
        )
        return path

    def test_a_manifest_that_states_its_release_names_it(self):
        manifest = self._manifest("PACKAGE RELEASE: 7\n")
        mod = _load_emitter()
        mod.emit_archive(manifest, self.chroot, self.out)
        self.assertEqual(
            sorted(p.name for p in self.out.iterdir()),
            ["demo-1.0-7.igos.tar.gz"])

    def test_a_manifest_without_a_release_keeps_the_release_less_name(self):
        manifest = self._manifest()
        mod = _load_emitter()
        mod.emit_archive(manifest, self.chroot, self.out)
        self.assertEqual(
            sorted(p.name for p in self.out.iterdir()),
            ["demo-1.0.igos.tar.gz"])


class TestTheEmittedHeaderAgreesWithTheName(unittest.TestCase):
    """The sealed header must state the release the filename carries."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.chroot = self.tmp / "chroot"
        (self.chroot / "usr/bin").mkdir(parents=True)
        (self.chroot / "usr/bin/demo").write_text("#!/bin/sh\nexit 0\n")
        self.manifests = self.tmp / "manifests"
        self.manifests.mkdir()
        self.out = self.tmp / "archives"

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_pkginfo_release_is_the_manifests_release(self):
        manifest = self.manifests / "demo-1.0"
        manifest.write_text(
            "PACKAGE NAME: demo-1.0\n"
            "PACKAGE VERSION: 1.0\n"
            "PACKAGE RELEASE: 7\n"
            "FILE LIST:\n"
            "usr/bin/demo\n"
        )
        mod = _load_emitter()
        mod.emit_archive(manifest, self.chroot, self.out)
        archive = self.out / "demo-1.0-7.igos.tar.gz"
        self.assertTrue(archive.is_file(),
                        sorted(p.name for p in self.out.iterdir()))
        import tarfile as _tarfile
        with _tarfile.open(archive, "r:gz") as tar:
            member = next(m for m in tar.getmembers()
                          if m.name.endswith(".PKGINFO"))
            text = tar.extractfile(member).read().decode()
        fields = dict(line.split("=", 1) for line in text.splitlines()
                      if "=" in line)
        self.assertEqual(
            fields.get("pkgrel"), "7",
            "the sealed header contradicts the release in the filename")


def _load_emitter():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "emit_package_archives", REPO_ROOT / "scripts/emit-package-archives.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    unittest.main()
