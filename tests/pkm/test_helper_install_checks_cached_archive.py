#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The helper-install path trusts a cached archive only when the signed index does.

`pkm install <app>` for a proprietary-download package runs _proprietary_install.
When the helper package's own database row is missing, that routine lays the
helper package down before it runs the helper — and it took whatever archive of
that name sat in the install root's archive directory and installed it with no
expected hash. install() refuses an archive it resolves ITSELF without a
verification reference (S5-1), but an archive the caller passes in is treated as
the caller's own trust decision, so that backstop never fired on this path: a
cached archive the signed index does not list, or lists with another sha256, was
deployed as root without a check.

The two other paths that can use a cached archive, a bare `pkm install` and
`pkm reinstall`, already apply the rule: the cached archive is used only when its
sha256 matches the signed index, and otherwise the verified download is. The
helper-install path applies the same rule, and passes the index's sha256 on to
install(), whose install-time re-hash then guards the cached archive as well.

Executed against the shipped routine with a real package database, a real
installer and real archives under a scratch install root. The repository is
stood in by the two answers the routine asks of it (the signed index's entry for
the package, and the verified download), and the helper run itself, the vendor's
download and license step that this change does not touch, by its outcome.
"""

import hashlib
import io
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pkm import cli, rootpaths
from pkm.database import PackageDB
from pkm.installer import PackageInstaller

APP = "demohelper"
VERSION = "1.0"
ORIGIN = Path("usr/share/demohelper/origin")


def _archive(directory: Path, origin: str) -> Path:
    """A real, well-formed .igos.tar.gz of APP whose one file names its origin."""
    lines = [
        f"pkgname={APP}", f"pkgver={VERSION}", "pkgrel=1",
        "pkgdesc=a download-helper package for the cached-archive tests",
        "license=GPL", "tier=extra", "builddate=2026-09-30T00:00:00Z",
        "size=64", "filecount=1",
    ]
    archive = directory / f"{APP}-{VERSION}.igos.tar.gz"
    payload = (origin + "\n").encode()
    with tarfile.open(archive, "w:gz") as tf:
        data = ("\n".join(lines) + "\n").encode()
        info = tarfile.TarInfo("./.PKGINFO")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo(f"./{ORIGIN}")
        info.size = len(payload)
        info.mode = 0o644
        tf.addfile(info, io.BytesIO(payload))
    return archive


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Repository:
    """The repository as the routine sees it: the signed index's entry for a
    package, and the verified download of it (the path of a real archive)."""

    def __init__(self, sha256=None, download=None):
        self.sha256 = sha256
        self.download = download
        self.fetched = []

    def get_package(self, name):
        if name != APP or self.sha256 is None:
            return None
        return {"name": APP, "version": VERSION, "release": 1,
                "sha256": self.sha256, "payload_license": "LicenseRef-Demo"}

    def download_package(self, name, reporter=None):
        self.fetched.append(name)
        if self.download is None:
            return False, f"{name}: no configured repository offers it"
        return True, str(self.download)


class _Terminal:
    def isatty(self):
        return True


class _Scratch:
    """A scratch install root holding a real package database and installer,
    with APP's cached archive in the root's archive directory and the
    repository's build of APP beside the root."""

    def __init__(self, tmp: str):
        tmp = Path(tmp)
        self.root = tmp / "target"
        (self.root / "etc").mkdir(parents=True)
        (self.root / "etc" / "passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
        (self.root / "etc" / "group").write_text("root:x:0:\n")
        archives = Path(rootpaths.archive_dir(self.root))
        archives.mkdir(parents=True)
        self.cached = _archive(archives, "the cached archive")
        downloads = tmp / "downloads"
        downloads.mkdir()
        self.verified = _archive(downloads, "the verified download")
        swapped = tmp / "swapped"
        swapped.mkdir()
        self.swapped = _archive(swapped, "an archive swapped in after the check")
        db_path = Path(rootpaths.db_path(self.root))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = PackageDB(str(db_path), root=str(self.root))
        self.installer = PackageInstaller(self.db, root=str(self.root))
        self.installs = []

    def close(self):
        self.db.close()

    def origin(self):
        """What the installed payload says it came from, or None."""
        path = self.root / ORIGIN
        return path.read_text().strip() if path.is_file() else None

    def install_helper(self, repository, replace=False, before_install=None):
        """Run the routine as `pkm install demohelper` reaches it; return its
        outcome and everything it said, as one line of words."""
        real_install = self.installer.install

        def recorded_install(name, **kwargs):
            self.installs.append(dict(kwargs, name=name))
            if before_install is not None:
                before_install()
            return real_install(name, **kwargs)

        self.installer.install = recorded_install
        self.installer._find_helper = lambda name: Path(f"/usr/bin/igos-install-{name}")
        self.installer._run_helper = lambda name, helper: (
            True, f"{name}: the application is installed", False)
        out, err = io.StringIO(), io.StringIO()
        reporter = cli.Reporter(stream=out, err_stream=err)
        with patch("pkm.cli.helper_payload_present", return_value=False), \
             patch("pkm.cli.acceptance_record_exists", return_value=True), \
             patch("pkm.cli.sys.stdin", _Terminal()), \
             patch("builtins.input", return_value="y"), \
             redirect_stdout(out):
            outcome = cli._proprietary_install(
                self.db, self.installer, repository, reporter, APP,
                "LicenseRef-Demo", replace=replace)
        said = " ".join((out.getvalue() + "\n" + err.getvalue()).split())
        return outcome, said


class TheHelperInstallChecksACachedArchive(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = _Scratch(self._tmp.name)

    def tearDown(self):
        self.s.close()
        self._tmp.cleanup()

    def test_a_cached_archive_the_index_does_not_list_is_not_installed(self):
        repository = _Repository(sha256=None, download=None)
        outcome, said = self.s.install_helper(repository)
        self.assertEqual(
            [], self.s.installs,
            "the cached archive was handed to the installer although the signed "
            "index does not list it: " + repr(self.s.installs))
        self.assertIsNone(self.s.db.get_installed(APP))
        self.assertIsNone(self.s.origin(), "the unverified archive was deployed")
        self.assertEqual("failed", outcome)
        self.assertIn(f"cached archive {self.s.cached.name} for {APP} is not in "
                      f"the signed index", said)
        self.assertIn("Nothing was installed.", said)

    def test_a_cached_archive_the_index_contradicts_gives_way_to_the_verified_download(self):
        repository = _Repository(sha256=_sha256(self.s.verified), download=self.s.verified)
        outcome, said = self.s.install_helper(repository)
        self.assertEqual([APP], repository.fetched)
        self.assertEqual(1, len(self.s.installs), repr(self.s.installs))
        self.assertEqual(str(self.s.verified), str(self.s.installs[0]["archive_path"]))
        self.assertEqual(_sha256(self.s.verified), self.s.installs[0]["expected_sha256"])
        self.assertEqual("the verified download", self.s.origin())
        self.assertEqual("ok", outcome)
        self.assertIn(f"cached archive {self.s.cached.name} does not match the "
                      f"signed index for {APP}", said)

    def test_a_cached_archive_the_index_vouches_for_is_installed_with_its_hash(self):
        repository = _Repository(sha256=_sha256(self.s.cached), download=None)
        outcome, said = self.s.install_helper(repository)
        self.assertEqual([], repository.fetched)
        self.assertEqual(1, len(self.s.installs), repr(self.s.installs))
        self.assertEqual(str(self.s.cached), str(self.s.installs[0]["archive_path"]))
        self.assertEqual(
            _sha256(self.s.cached), self.s.installs[0]["expected_sha256"],
            "the cached archive was installed without the index's sha256, so the "
            "install-time re-hash could not guard it")
        self.assertEqual("the cached archive", self.s.origin())
        self.assertEqual("ok", outcome)
        self.assertIn(f"cached archive {self.s.cached.name} matches the signed index", said)

    def test_a_cached_archive_swapped_after_the_check_is_refused_at_install(self):
        repository = _Repository(sha256=_sha256(self.s.cached), download=None)

        def swap():
            self.s.cached.write_bytes(self.s.swapped.read_bytes())

        outcome, said = self.s.install_helper(repository, before_install=swap)
        self.assertIsNone(self.s.origin(), "the archive swapped in after the check "
                                           "was deployed")
        self.assertIsNone(self.s.db.get_installed(APP))
        self.assertEqual("failed", outcome)
        self.assertIn("Archive integrity check FAILED", said)


class WhatTheChangeLeavesAlone(unittest.TestCase):
    """Controls: no cached archive, and a helper whose row is already present."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = _Scratch(self._tmp.name)

    def tearDown(self):
        self.s.close()
        self._tmp.cleanup()

    def test_with_no_cached_archive_the_verified_download_is_installed(self):
        self.s.cached.unlink()
        repository = _Repository(sha256=_sha256(self.s.verified), download=self.s.verified)
        outcome, said = self.s.install_helper(repository)
        self.assertEqual([APP], repository.fetched)
        self.assertEqual(1, len(self.s.installs), repr(self.s.installs))
        self.assertEqual(_sha256(self.s.verified), self.s.installs[0]["expected_sha256"])
        self.assertEqual("the verified download", self.s.origin())
        self.assertEqual("ok", outcome)

    def test_with_nothing_cached_and_nothing_offered_the_message_is_unchanged(self):
        self.s.cached.unlink()
        outcome, said = self.s.install_helper(_Repository(sha256=None, download=None))
        self.assertEqual("failed", outcome)
        self.assertEqual([], self.s.installs)
        self.assertIn(f"'{APP}' is not available locally or from any configured "
                      f"repository.", said)

    def test_a_helper_whose_row_is_present_is_not_laid_down_again(self):
        # The two callers that reach the routine with the row present: the
        # payload step after a deploy, and `pkm reinstall <app>` (replace=True).
        ok, msg = self.s.installer.install(
            APP, archive_path=str(self.s.verified),
            expected_sha256=_sha256(self.s.verified))
        self.assertTrue(ok, msg)
        for replace in (False, True):
            repository = _Repository(sha256=None, download=None)
            outcome, said = self.s.install_helper(repository, replace=replace)
            self.assertEqual("ok", outcome, said)
            self.assertEqual([], self.s.installs)
            self.assertEqual([], repository.fetched)
            self.assertNotIn("cached archive", said)
            self.assertEqual("the verified download", self.s.origin())


if __name__ == "__main__":
    unittest.main()
