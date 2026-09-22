# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A rebuild of one version leaves one archive of that version behind it.

Before 2026-09-22 a rebuild of one version wrote the same
<name>-<version>.igos.tar.gz and so replaced the earlier build in place: a
build directory held one archive per version. The name now carries the
release, so the earlier build is no longer overwritten. A lineage build
substrate holds every archive under the release-less name, so every package
rebuilt there at an unchanged version would keep BOTH builds; the manifest
phase signs every archive in the directory, the squashfs ships them, and the
metadata gate halts the image on each one.

Once a build has passed every gate, the builder (Python tiers) and the bash
tier's pkg_install now remove an earlier build of the same version, which is
what the overwrite did. A file is removed only when its name is one a build of
exactly that name and version carries AND its own sealed header states that
package and version -- the header is the proof, because a package whose
upstream version ends in what reads as a release composes the same text as
another version of the same name with a release.

These cases run the module rule, the builder's own method against real
archives in a scratch directory, and the shell function extracted from
scripts/pkg-functions.sh as a real bash process under set -euo pipefail.
"""
import importlib
import io
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from .factories import make_package  # noqa: E402
# The module, not the new name: at a tree without the rule this file
# still collects, and each case fails on the missing behaviour.
import pkm.archive_names as archive_names  # noqa: E402

_builder_mod = importlib.import_module("igos-build.builder")
BuildExecutor = _builder_mod.BuildExecutor


def write_archive(path: Path, name: str, version: str, release) -> None:
    """A real .igos.tar.gz whose sealed header states the given build."""
    with tarfile.open(path, "w:gz") as tar:
        header = f"pkgname={name}\npkgver={version}\npkgrel={release}\n".encode()
        info = tarfile.TarInfo("./.PKGINFO")
        info.size = len(header)
        tar.addfile(info, io.BytesIO(header))
        payload = f"{name} {version} {release}\n".encode()
        info = tarfile.TarInfo(f"./usr/share/{name}/build")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))


class TheModuleRule(unittest.TestCase):
    def test_only_names_a_build_of_exactly_this_version_carries(self):
        present = [
            "demo-1.0.igos.tar.gz",             # banked before the change
            "demo-1.0-6.igos.tar.gz",           # an earlier release
            "demo-1.0-7.igos.tar.gz",           # this build
            "demo-1.0.1-1.igos.tar.gz",         # another version
            "demo-0.9-3.igos.tar.gz",           # a version-bump twin
            "demo-extra-1.0-1.igos.tar.gz",     # another package
            "demo-1.0-07.igos.tar.gz",          # not how a release is written
            "demo-1.0-7.igos.tar.gz.failed",    # a quarantined build
            "other-1.0-1.igos.tar.gz",
        ]
        self.assertEqual(
            archive_names.same_version_filenames("demo", "1.0", present),
            ["demo-1.0.igos.tar.gz", "demo-1.0-6.igos.tar.gz",
             "demo-1.0-7.igos.tar.gz"])


class _Logger:
    def __init__(self):
        self.lines = []

    def error(self, msg):
        self.lines.append(("error", msg))

    def warning(self, msg):
        self.lines.append(("warning", msg))

    def info(self, msg):
        self.lines.append(("info", msg))


class TheBuilderRetiresTheEarlierBuild(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.archives = Path(self._tmp.name) / "archives"
        self.archives.mkdir()
        self.stub = SimpleNamespace(pkg_archives=self.archives, logger=_Logger())

    def retire(self, pkg):
        BuildExecutor._retire_superseded_archives(self.stub, pkg)

    def names(self):
        return sorted(p.name for p in self.archives.iterdir())

    def test_the_release_less_build_of_the_same_version_is_removed(self):
        write_archive(self.archives / "demo-1.0-7.igos.tar.gz", "demo", "1.0", 7)
        write_archive(self.archives / "demo-1.0.igos.tar.gz", "demo", "1.0", 5)
        self.retire(make_package("demo", "1.0", release=7))
        self.assertEqual(self.names(), ["demo-1.0-7.igos.tar.gz"])
        info = [m for level, m in self.stub.logger.lines if level == "info"]
        self.assertEqual(len(info), 1, self.stub.logger.lines)
        self.assertIn("removed the earlier build", info[0])
        self.assertIn("release 5", info[0])

    def test_an_earlier_release_of_the_same_version_is_removed(self):
        write_archive(self.archives / "demo-1.0-7.igos.tar.gz", "demo", "1.0", 7)
        write_archive(self.archives / "demo-1.0-6.igos.tar.gz", "demo", "1.0", 6)
        self.retire(make_package("demo", "1.0", release=7))
        self.assertEqual(self.names(), ["demo-1.0-7.igos.tar.gz"])

    def test_other_versions_and_other_packages_are_never_touched(self):
        write_archive(self.archives / "demo-1.0-7.igos.tar.gz", "demo", "1.0", 7)
        write_archive(self.archives / "demo-0.9-3.igos.tar.gz", "demo", "0.9", 3)
        write_archive(self.archives / "demo-extra-1.0-1.igos.tar.gz",
                      "demo-extra", "1.0", 1)
        self.retire(make_package("demo", "1.0", release=7))
        self.assertEqual(self.names(), ["demo-0.9-3.igos.tar.gz",
                                        "demo-1.0-7.igos.tar.gz",
                                        "demo-extra-1.0-1.igos.tar.gz"])

    def test_a_file_whose_header_states_another_version_stays(self):
        # imagemagick 7.1.2-13 banked under its release-less name composes the
        # same text as imagemagick 7.1.2 release 13: only the header tells.
        write_archive(self.archives / "imagemagick-7.1.2-1.igos.tar.gz",
                      "imagemagick", "7.1.2", 1)
        write_archive(self.archives / "imagemagick-7.1.2-13.igos.tar.gz",
                      "imagemagick", "7.1.2-13", 1)
        self.retire(make_package("imagemagick", "7.1.2", release=1))
        self.assertEqual(self.names(), ["imagemagick-7.1.2-1.igos.tar.gz",
                                        "imagemagick-7.1.2-13.igos.tar.gz"])
        warnings = [m for level, m in self.stub.logger.lines if level == "warning"]
        self.assertTrue(any("imagemagick 7.1.2-13, not imagemagick 7.1.2" in m
                            for m in warnings), self.stub.logger.lines)

    def test_a_file_whose_header_names_another_package_stays(self):
        write_archive(self.archives / "demo-1.0-7.igos.tar.gz", "demo", "1.0", 7)
        write_archive(self.archives / "demo-1.0.igos.tar.gz", "impostor", "1.0", 1)
        self.retire(make_package("demo", "1.0", release=7))
        self.assertIn("demo-1.0.igos.tar.gz", self.names())

    def test_a_file_whose_header_cannot_be_read_stays_and_is_named(self):
        write_archive(self.archives / "demo-1.0-7.igos.tar.gz", "demo", "1.0", 7)
        (self.archives / "demo-1.0.igos.tar.gz").write_bytes(b"not an archive")
        self.retire(make_package("demo", "1.0", release=7))
        self.assertIn("demo-1.0.igos.tar.gz", self.names())
        self.assertTrue(any(level == "warning" and "cannot be read" in m
                            for level, m in self.stub.logger.lines))

    def test_nothing_is_removed_when_this_build_left_no_archive(self):
        write_archive(self.archives / "demo-1.0.igos.tar.gz", "demo", "1.0", 5)
        self.retire(make_package("demo", "1.0", release=7))
        self.assertEqual(self.names(), ["demo-1.0.igos.tar.gz"])

    def test_a_version_whose_tail_reads_as_a_release_keeps_its_own_archive(self):
        write_archive(self.archives / "dialog-1.3-20260107-2.igos.tar.gz",
                      "dialog", "1.3-20260107", 2)
        write_archive(self.archives / "dialog-1.3-20260107.igos.tar.gz",
                      "dialog", "1.3-20260107", 1)
        self.retire(make_package("dialog", "1.3-20260107", release=2))
        self.assertEqual(self.names(), ["dialog-1.3-20260107-2.igos.tar.gz"])


class TheBuilderCallsItOnlyOnSuccess(unittest.TestCase):
    """The call sits beside the failure path's cleanup, and only a build that
    passed its gates retires anything."""

    def test_the_success_branch_calls_the_retire_step(self):
        source = (REPO_ROOT / "igos-build" / "builder.py").read_text()
        failure = source.index("self._remove_failed_tracking_artifacts(pkg)")
        branch = source[failure:failure + 700]
        self.assertRegex(
            branch,
            r"elif success and self\.tracked and not pkg\.skip_tracking:\s*\n"
            r"\s*self\._retire_superseded_archives\(pkg\)")


def _shell_function(name: str) -> str:
    text = (REPO_ROOT / "scripts/pkg-functions.sh").read_text()
    match = re.search(r"^%s\(\) \{\n.*?^\}$" % re.escape(name), text, re.M | re.S)
    if not match:
        raise AssertionError(f"{name} missing from scripts/pkg-functions.sh")
    return match.group()


class TheBashTierRetiresTheEarlierBuild(unittest.TestCase):
    """The shell function, run as a real bash process under set -euo pipefail
    with a PATH carrying only the tools it uses."""

    TOOLS = ("bash", "tar", "gzip", "sed", "head", "rm", "printf")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for tool in self.TOOLS:
            found = shutil.which(tool)
            if found:
                (self.bin / tool).symlink_to(found)
        self.archives = self.tmp / "archives"
        self.archives.mkdir()

    def run_retire(self, *args):
        script = self.tmp / "run.sh"
        script.write_text(
            "set -euo pipefail\n"
            f'IGOS_PKG_ARCHIVES="{self.archives}"\n'
            'pkg_log() { printf "%s\\n" "$*"; }\n'
            'pkg_error() { printf "%s\\n" "$*" >&2; }\n'
            + _shell_function("pkg_retire_superseded_archives") + "\n"
            + "pkg_retire_superseded_archives " + " ".join(args)
            + "\necho RC=$?\n")
        return subprocess.run([shutil.which("bash"), str(script)],
                              env={"PATH": str(self.bin)},
                              capture_output=True, text=True, timeout=60)

    def names(self):
        return sorted(p.name for p in self.archives.iterdir())

    def test_the_earlier_builds_of_the_version_are_removed(self):
        write_archive(self.archives / "demo-1.0-7.igos.tar.gz", "demo", "1.0", 7)
        write_archive(self.archives / "demo-1.0.igos.tar.gz", "demo", "1.0", 5)
        write_archive(self.archives / "demo-1.0-6.igos.tar.gz", "demo", "1.0", 6)
        write_archive(self.archives / "demo-0.9-3.igos.tar.gz", "demo", "0.9", 3)
        r = self.run_retire("demo", "1.0", "7")
        self.assertIn("RC=0", r.stdout, r.stdout + r.stderr)
        self.assertEqual(self.names(), ["demo-0.9-3.igos.tar.gz",
                                        "demo-1.0-7.igos.tar.gz"])
        self.assertEqual(r.stdout.count("removed the earlier build"), 2, r.stdout)

    def test_what_the_header_does_not_prove_stays(self):
        write_archive(self.archives / "demo-1.0-7.igos.tar.gz", "demo", "1.0", 7)
        write_archive(self.archives / "demo-1.0.igos.tar.gz", "impostor", "1.0", 1)
        (self.archives / "demo-1.0-6.igos.tar.gz").write_bytes(b"not an archive")
        r = self.run_retire("demo", "1.0", "7")
        self.assertIn("RC=0", r.stdout, r.stdout + r.stderr)
        self.assertEqual(self.names(), ["demo-1.0-6.igos.tar.gz",
                                        "demo-1.0-7.igos.tar.gz",
                                        "demo-1.0.igos.tar.gz"])
        self.assertIn("header states impostor 1.0, not demo 1.0", r.stdout)
        self.assertIn("header cannot be read", r.stdout)

    def test_an_unstated_release_keeps_the_release_less_archive(self):
        # The recipe-less packages this tier builds write the release-less
        # name; it is the build's own archive, never an earlier one.
        write_archive(self.archives / "demo-1.0.igos.tar.gz", "demo", "1.0", 1)
        r = self.run_retire("demo", "1.0")
        self.assertIn("RC=0", r.stdout, r.stdout + r.stderr)
        self.assertEqual(self.names(), ["demo-1.0.igos.tar.gz"])

    def test_nothing_is_removed_when_this_build_left_no_archive(self):
        write_archive(self.archives / "demo-1.0.igos.tar.gz", "demo", "1.0", 5)
        r = self.run_retire("demo", "1.0", "7")
        self.assertIn("RC=0", r.stdout, r.stdout + r.stderr)
        self.assertEqual(self.names(), ["demo-1.0.igos.tar.gz"])

    def test_pkg_install_calls_it_after_registration_and_before_cleanup(self):
        body = _shell_function("pkg_install")
        call = body.index('pkg_retire_superseded_archives "$name" "$version" "$release"')
        self.assertLess(body.index("pkg_run_pkm_single_flight import"), call)
        self.assertLess(call, body.index('pkg_cleanup "$name" "$version"'))


if __name__ == "__main__":
    unittest.main()
