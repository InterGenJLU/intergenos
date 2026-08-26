#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""S5 (security-review 2026-07-01) — the ungated ARCHIVE_DIR install seam.

Two facets, both closed here:

  S5-1  A bare `pkm install <name>` / `pkm reinstall <name>` that resolves a
        cached archive from /var/lib/igos/archives installed it with NO signature
        / checksum / trust check — the `expected_sha256` gate was simply skipped,
        while every network-facing path (repo-fetch, `--archive`) enforced it. A
        gate that exists must not be skippable by which code path reached it. Now:
        the CLI verifies the cache against the signed index and only installs it
        when it matches; install() itself fail-closes on any locally-resolved
        archive that arrives without a verification reference.

  S5-2  `_find_archive` matched by `startswith(f"{name}-")` over a reverse-SORTED
        listing — so `pkm install bash` could select `bash-completion-*` (name
        confusion) and a lexically-greater filename won over the real version.
        Now: exact name parse + highest version by pkm's version.compare.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pkm.installer import PackageInstaller
from pkm import cli


def _archives_under(root: Path) -> Path:
    """The archive directory pkm resolves for an install root.

    The seam these tests pin used to be the module constant ARCHIVE_DIR. It is
    now derived from the installer's own root (pkm/rootpaths.py), so the
    fixtures build the real layout under a temporary root and the tests
    exercise the real resolution instead of a patched name.
    """
    d = root / "var" / "lib" / "igos" / "archives"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _touch(d: Path, name: str) -> None:
    (d / name).write_bytes(b"x")


class FindArchiveExactMatch(unittest.TestCase):
    """S5-2: exact name + highest-version selection, no prefix confusion."""

    def _installer_over(self, root: Path) -> PackageInstaller:
        inst = PackageInstaller.__new__(PackageInstaller)  # no DB needed here
        inst.root = Path(root)
        return inst

    def test_name_prefix_confusion_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            d = _archives_under(root)
            for n in ("bash-5.2.37.igos.tar.gz",
                      "bash-completion-2.11.igos.tar.gz",
                      "go-1.26.4.igos.tar.gz",
                      "go-md2man-2.0.5.igos.tar.gz"):
                _touch(d, n)
            inst = self._installer_over(root)
            self.assertEqual(inst._find_archive("bash").name,
                             "bash-5.2.37.igos.tar.gz")
            self.assertEqual(inst._find_archive("go").name,
                             "go-1.26.4.igos.tar.gz")
            # the longer names still resolve for their OWN exact name
            self.assertEqual(inst._find_archive("bash-completion").name,
                             "bash-completion-2.11.igos.tar.gz")
            self.assertEqual(inst._find_archive("go-md2man").name,
                             "go-md2man-2.0.5.igos.tar.gz")

    def test_highest_version_by_compare_not_lexical(self):
        # Lexical reverse-sort would pick "5.2.9" over "5.2.37"; version.compare
        # (numeric) must pick 5.2.37.
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            d = _archives_under(root)
            _touch(d, "bash-5.2.9.igos.tar.gz")
            _touch(d, "bash-5.2.37.igos.tar.gz")
            inst = self._installer_over(root)
            self.assertEqual(inst._find_archive("bash").name,
                             "bash-5.2.37.igos.tar.gz")

    def test_no_match_returns_none(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            d = _archives_under(root)
            _touch(d, "bash-5.2.37.igos.tar.gz")
            inst = self._installer_over(root)
            self.assertIsNone(inst._find_archive("zsh"))
            # a name that is only a prefix (no version after it) never matches
            self.assertIsNone(inst._find_archive("ba"))


class InstallBackstopFailsClosed(unittest.TestCase):
    """S5-1: install() refuses a locally-resolved archive with no verification
    reference, and does NOT fire for a caller-provided archive_path."""

    def _installer(self, root: Path):
        db = MagicMock()
        db.get_installed.return_value = None  # not already installed
        inst = PackageInstaller.__new__(PackageInstaller)
        inst.db = db
        inst.root = Path(root)
        return inst

    def test_resolved_local_without_sha_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            d = _archives_under(root)
            _touch(d, "foo-1.0.igos.tar.gz")
            inst = self._installer(root)
            ok, msg = inst.install("foo", archive_path=None,
                                   expected_sha256=None)
        self.assertFalse(ok)
        self.assertIn("no signed-index verification reference", msg)

    def test_no_local_archive_reports_not_found_not_backstop(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _archives_under(root)
            inst = self._installer(root)
            ok, msg = inst.install("foo", archive_path=None,
                                   expected_sha256=None)
        self.assertFalse(ok)
        self.assertIn("No archive found", msg)
        self.assertNotIn("verification reference", msg)

    def test_explicit_archive_path_bypasses_the_backstop(self):
        # A caller-PROVIDED archive_path is the caller's own trust decision, so the
        # backstop must not fire — the install proceeds past it (and here fails
        # later on the dummy archive content, which is a DIFFERENT error).
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            d = _archives_under(root)
            arch = d / "foo-1.0.igos.tar.gz"
            arch.write_bytes(b"not a real tar")
            inst = self._installer(root)
            ok, msg = inst.install("foo", archive_path=str(arch),
                                   expected_sha256=None)
        self.assertFalse(ok)
        self.assertNotIn("no signed-index verification reference", msg)


class CmdReinstallVerifiesCache(unittest.TestCase):
    """S5-1 at the CLI: a cached archive whose sha256 does not match the signed
    index is discarded (never reinstalled unverified)."""

    def _args(self, *names):
        import argparse
        return argparse.Namespace(packages=list(names), yes=True, quiet=True,
                                  verbose=False, json=False)

    def test_mismatched_cache_is_discarded_and_refetched(self):
        db = MagicMock()
        db.get_installed.return_value = {"name": "bash", "version": "5.2"}
        with patch("pkm.cli.is_download_helper", return_value=False), \
             patch("pkm.cli._proprietary_install"), \
             patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.PackageRemover") as Remover, \
             patch("pkm.cli._sha256", return_value="localsha"), \
             patch("pkm.cli.RepoManager") as Repo:
            Installer.return_value._find_archive.return_value = Path(
                "/var/lib/igos/archives/bash-9.9.9.igos.tar.gz")
            # Index says the real bash has a DIFFERENT sha — the cache is a
            # mismatch, so it must be discarded and the verified package fetched.
            Repo.return_value.get_package.return_value = {"sha256": "indexsha"}
            Repo.return_value.download_package.return_value = (
                True, "/var/cache/pkm/pkgs/bash-5.2.igos.tar.gz")
            Remover.return_value.remove.return_value = (True, "removed")
            Installer.return_value.install.return_value = (True, "installed")
            cli.cmd_reinstall(db, self._args("bash"))
            # install() was called with the DOWNLOADED archive + the index sha,
            # never the mismatched local cache.
            call = Installer.return_value.install.call_args
            self.assertEqual(call.kwargs.get("expected_sha256"), "indexsha")
            self.assertIn("bash-5.2", str(call.kwargs.get("archive_path")))
            Repo.return_value.download_package.assert_called_once()


if __name__ == "__main__":
    unittest.main()
