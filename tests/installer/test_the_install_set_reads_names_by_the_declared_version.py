# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The install set reads every archive name by the version its recipe declares.

An archive is named ``<name>-<version>[-<release>].igos.tar.gz``, and where
the name ends cannot be read from the text alone. ``llama-cpp-cuda-b8796-8``
is the package llama-cpp-cuda at version b8796, release 8; split at the first
hyphen followed by a digit it reads as a package called llama-cpp-cuda-b8796
at version 8, which no packages/<tier>/ directory names, so the strict-tier
install-set audit refused the image and Forge could never have installed
either GPU engine.

``get_archives`` now reads each name by the versions the recipes declare,
else by the archive's own ``.PKGINFO``, and refuses by name an archive that
neither accounts for. These cases build real gzip archives with a real
``.PKGINFO``, read the real recipe tree, and run the real audit script as a
program.
"""

import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.backend import packages as backend  # noqa: E402
from pkm.archive_names import archive_filename  # noqa: E402

AUDIT = REPO_ROOT / "scripts" / "preflight-install-set.py"


def _archive(path: Path, name, version, release, with_pkginfo=True):
    """A real gzip archive; the .PKGINFO is its last member, as the builder
    writes it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        payload = b"payload\n"
        member = tarfile.TarInfo(f"./usr/bin/{name}")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
        if with_pkginfo:
            body = (f"pkgname={name}\npkgver={version}\npkgrel={release}\n"
                    ).encode()
            info = tarfile.TarInfo("./.PKGINFO")
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))


def _recipe(packages: Path, tier, name, version=None, release=1, extra=""):
    """packages/<tier>/<name>/package.yml; version=None writes the directory
    without a package.yml (the shape an ISO may carry)."""
    d = packages / tier / name
    d.mkdir(parents=True, exist_ok=True)
    if version is not None:
        (d / "package.yml").write_text(
            f'name: {name}\nversion: "{version}"\nrelease: {release}\n'
            f"tier: {tier}\n{extra}")


def _audit(archives: Path, packages: Path):
    return subprocess.run(
        [sys.executable, "-B", str(AUDIT), "--archive-dir", str(archives),
         "--packages-dir", str(packages), "--strict-tier"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)


class _Store(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.archives = self.tmp / "archives"
        self.packages = self.tmp / "packages"
        self.archives.mkdir()
        self.packages.mkdir()

    def tearDown(self):
        self._tmp.cleanup()


class TestTheTwoGpuEnginesAreReadByTheirDeclaredVersion(_Store):
    """The two names the image's strict-tier audit refused."""

    def setUp(self):
        super().setUp()
        _recipe(self.packages, "compute", "llama-cpp-cuda", "b8796", 8)
        _recipe(self.packages, "compute", "llama-cpp-hip", "b8796", 7)
        _archive(self.archives / "llama-cpp-cuda-b8796-8.igos.tar.gz",
                 "llama-cpp-cuda", "b8796", 8)
        _archive(self.archives / "llama-cpp-hip-b8796-7.igos.tar.gz",
                 "llama-cpp-hip", "b8796", 7)

    def test_each_engine_is_read_by_its_recipe(self):
        found = backend.get_archives(self.archives, self.packages)
        self.assertEqual(sorted(found), ["llama-cpp-cuda", "llama-cpp-hip"])
        self.assertEqual(found["llama-cpp-cuda"][0], "b8796")
        self.assertEqual(found["llama-cpp-hip"][0], "b8796")

    def test_forge_pulls_both_engines_into_the_install_set(self):
        # The engines live in the compute tier, which no group selects; an
        # ai-tier package that depends on them pulls them in, by name.
        _recipe(self.packages, "ai", "assistant", "1.0", 1,
                "dependencies:\n  runtime:\n    - llama-cpp-cuda\n"
                "    - llama-cpp-hip\n")
        _archive(self.archives / "assistant-1.0-1.igos.tar.gz",
                 "assistant", "1.0", 1)
        out = backend.get_group_packages(["ai"], self.archives, self.packages,
                                         detected_vendors=set())
        got = {(n, v) for (n, v, _p) in out}
        self.assertIn(("llama-cpp-cuda", "b8796"), got)
        self.assertIn(("llama-cpp-hip", "b8796"), got)

    def test_the_strict_tier_audit_passes_both_engines(self):
        run = _audit(self.archives, self.packages)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertNotIn("TIER-ORPHAN", run.stderr)
        self.assertIn("2 staged archives all parsed", run.stdout)


class TestANameNoRecipeAccountsForIsReadFromItsPkginfo(_Store):
    """The second source: the archive's own .PKGINFO."""

    def test_the_engines_are_read_from_their_pkginfo_without_recipes(self):
        _archive(self.archives / "llama-cpp-cuda-b8796-8.igos.tar.gz",
                 "llama-cpp-cuda", "b8796", 8)
        _archive(self.archives / "llama-cpp-hip-b8796-7.igos.tar.gz",
                 "llama-cpp-hip", "b8796", 7)
        found = backend.get_archives(self.archives)
        self.assertEqual(found.get("llama-cpp-cuda", (None,))[0], "b8796",
                         sorted(found))
        self.assertEqual(found.get("llama-cpp-hip", (None,))[0], "b8796",
                         sorted(found))

    def test_a_version_its_recipe_no_longer_declares(self):
        # An earlier build left in the store: the recipe says 2.0 and cannot
        # account for demo-1.9-3, whose .PKGINFO can.
        _recipe(self.packages, "extra", "demo", "2.0", 1)
        _archive(self.archives / "demo-1.9-3.igos.tar.gz", "demo", "1.9", 3)
        found = backend.get_archives(self.archives, self.packages)
        self.assertEqual(found["demo"][0], "1.9")


class TestANameNothingAccountsForIsRefusedByName(_Store):
    """No recipe declares its version and it carries no .PKGINFO: the name
    could only be guessed, so it is refused by name, loudly."""

    def setUp(self):
        super().setUp()
        # The directory is there (an ISO may carry one without its
        # package.yml), so a guessed name would pass the tier check.
        _recipe(self.packages, "core", "early", None)
        _archive(self.archives / "early-1.2.igos.tar.gz", "early", "1.2", 1,
                 with_pkginfo=False)

    def test_get_archives_reports_it_at_error_and_does_not_yield_it(self):
        with self.assertLogs(backend.LOG, level="ERROR") as logs:
            found = backend.get_archives(self.archives, self.packages)
        self.assertNotIn("early", found)
        self.assertEqual(found, {})
        self.assertTrue(any("early-1.2.igos.tar.gz" in line and "ERROR" in line
                            for line in logs.output), logs.output)

    def test_the_audit_names_it_and_fails(self):
        run = _audit(self.archives, self.packages)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("ERROR: get_archives: 'early-1.2.igos.tar.gz'",
                      run.stderr)
        self.assertIn("DROPPED: early-1.2.igos.tar.gz", run.stderr)


class TestEveryRecipeInThisTreeIsReadBackByItsDeclaredVersion(unittest.TestCase):
    """Every recipe in this tree, in both name shapes the store holds: the
    release-less name every archive built before the name carried the release
    has, and the release-carrying name. Empty files: the declared version
    reads each, so no .PKGINFO is opened."""

    def test_both_name_shapes_of_every_recipe(self):
        import yaml
        packages = REPO_ROOT / "packages"
        expected = {}
        for yml in sorted(packages.rglob("package.yml")):
            data = yaml.safe_load(yml.read_text()) or {}
            if not isinstance(data, dict) or data.get("version") is None:
                continue
            for key in (data.get("name"), data.get("ships_as")):
                if key:
                    expected.setdefault(str(key), str(data["version"]))
        self.assertGreater(len(expected), 1000, "the recipe tree was not read")
        with tempfile.TemporaryDirectory() as tmp:
            for shape, release in (("release-less", None),
                                   ("release-carrying", 3)):
                store = Path(tmp) / shape
                store.mkdir()
                for name, version in expected.items():
                    (store / archive_filename(name, version, release)
                     ).write_bytes(b"")
                found = backend.get_archives(store, packages)
                got = {name: version for name, (version, _p) in found.items()}
                self.assertEqual(got, expected, shape)


if __name__ == "__main__":
    unittest.main()
