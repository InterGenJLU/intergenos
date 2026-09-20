#!/usr/bin/env python3
"""A pre-upgrade rollback copy is the release it claims to be, or it is nothing.

Measured on three installed machines 2026-09-19, in two shapes. The file holds
the MIRROR'S STALE release: /var/cache/pkm/rollback/ held four pkm archives
named -71, -73, -76 and -86 that were byte-identical and all carried pkgrel=73,
and five intergen archives named -245 through -285 that all carried pkgrel=248.
Or the file holds the release that REPLACED the one it is named for:
pkm-0.2.0-71 carrying 72, forge-1.0.0-240 carrying 241, cuda-toolkit-13.3.1-5
carrying 6. One cause for both — the destination filename is built from the
release being replaced, while the source is taken from the download cache on
filename alone, and the live index publishes release-LESS filenames, so the
file that always matches is whatever build the mirror last served, which on a
mirror-driven upgrade is the incoming one.

What that costs: the file exists to be restored from when an upgrade fails, by
someone who is already in trouble. Restoring it moves the machine to a release
nobody chose, under a filename that promised the release they had. A safety net
that names a release it does not carry is worse than no net, because it is
believed.

These tests fix the rule: the copy is taken only from an archive whose own
.PKGINFO names the package, version and release being replaced; when no such
archive exists the transaction says so in place of writing a file; and anything
reading that cache refuses a file whose content contradicts its name.
"""

import io
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pkm.repo
from pkm import cli


def _archive(path, name="pkm", version="0.2.0", release=73, extra=b""):
    """Write a minimal .igos.tar.gz whose ./.PKGINFO names this release."""
    pkginfo = (
        f"pkgname={name}\n"
        f"pkgver={version}\n"
        f"pkgrel={release}\n"
        f"filecount=1\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo("./.PKGINFO")
        info.size = len(pkginfo)
        tf.addfile(info, io.BytesIO(pkginfo))
        payload = b"payload" + extra
        info = tarfile.TarInfo("./usr/bin/stub")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return path


class RollbackCopyCarriesItsRelease(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.pkg_cache = self.tmp / "packages"
        self.rollback = self.tmp / "rollback"
        self.pkg_cache.mkdir()
        self.rollback.mkdir()
        self._p1 = patch.object(pkm.repo, "REPO_PKG_CACHE", self.pkg_cache)
        self._p2 = patch.object(pkm.repo, "REPO_ROLLBACK_DIR", self.rollback)
        self._p1.start()
        self._p2.start()
        self.addCleanup(self._p1.stop)
        self.addCleanup(self._p2.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_a_cached_archive_of_another_release_is_not_a_rollback_source(self):
        """The measured defect: an r73 archive must not be saved as "-86"."""
        # The download cache holds the mirror's release-less file, which is r73.
        _archive(self.pkg_cache / "pkm-0.2.0.igos.tar.gz", release=73)

        saved = cli._save_rollback_archive("pkm", "0.2.0", 86)

        self.assertIsNone(
            saved,
            "an archive carrying release 73 was accepted as the rollback copy "
            "of release 86",
        )
        self.assertEqual(
            list(self.rollback.glob("*.igos.tar.gz")), [],
            "a rollback file was written for a release the archive does not "
            "carry",
        )

    def test_a_cached_archive_of_the_replaced_release_is_saved(self):
        """The honest case still works, and the saved copy still carries it."""
        _archive(self.pkg_cache / "pkm-0.2.0.igos.tar.gz", release=86)

        saved = cli._save_rollback_archive("pkm", "0.2.0", 86)

        self.assertIsNotNone(saved, "a matching archive was refused")
        dest, sha = saved
        self.assertEqual(dest.name, "pkm-0.2.0-86.igos.tar.gz")
        self.assertTrue(dest.exists())
        self.assertEqual(len(sha), 64)
        with tarfile.open(dest) as tf:
            body = tf.extractfile("./.PKGINFO").read().decode()
        self.assertIn("pkgrel=86", body)

    def test_a_release_qualified_cache_file_of_the_wrong_release_is_refused(self):
        """The name on the cache file is not evidence either — read the content."""
        _archive(self.pkg_cache / "pkm-0.2.0-86.igos.tar.gz", release=73)

        self.assertIsNone(cli._save_rollback_archive("pkm", "0.2.0", 86))

    def test_the_cached_file_holding_the_NEW_release_is_refused(self):
        """The second measured shape, and the commoner one.

        On a mirror-driven upgrade the download cache's release-less file IS
        the incoming archive by the time the copy is taken, so the "old"
        copy is the NEW release under the old release's name. Read on other
        machines 2026-09-19: pkm-0.2.0-71 held 72, forge-1.0.0-240 held 241,
        cuda-toolkit-13.3.1-5 held 6 and -1 held 5. Restoring one of those
        does not undo the upgrade — it repeats it, while the filename
        promises the release the person is trying to get back to.
        """
        _archive(self.pkg_cache / "pkm-0.2.0.igos.tar.gz", release=90)

        saved = cli._save_rollback_archive("pkm", "0.2.0", 89)

        self.assertIsNone(
            saved,
            "the incoming release was accepted as the rollback copy of the "
            "release it replaces",
        )
        self.assertEqual(list(self.rollback.glob("*.igos.tar.gz")), [])

    def test_an_archive_for_another_package_is_refused(self):
        _archive(self.pkg_cache / "pkm-0.2.0.igos.tar.gz",
                 name="forge", release=86)

        self.assertIsNone(cli._save_rollback_archive("pkm", "0.2.0", 86))

    def test_an_archive_of_another_version_is_refused(self):
        _archive(self.pkg_cache / "pkm-0.2.0.igos.tar.gz",
                 version="0.1.9", release=86)

        self.assertIsNone(cli._save_rollback_archive("pkm", "0.2.0", 86))

    def test_an_unreadable_archive_is_refused_rather_than_trusted(self):
        (self.pkg_cache / "pkm-0.2.0.igos.tar.gz").write_bytes(b"not a tarball")

        self.assertIsNone(cli._save_rollback_archive("pkm", "0.2.0", 86))


class ReadingTheCacheRefusesAMislabelledFile(unittest.TestCase):
    """Whatever consumes the cache checks the file against its own name."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_file_whose_pkginfo_release_differs_from_its_name_is_refused(self):
        p = _archive(self.tmp / "pkm-0.2.0-86.igos.tar.gz", release=73)

        ok, why = cli.rollback_archive_matches_its_name(p)

        self.assertFalse(ok)
        self.assertIn("73", why)
        self.assertIn("86", why)

    def test_a_file_that_matches_its_name_is_accepted(self):
        p = _archive(self.tmp / "pkm-0.2.0-86.igos.tar.gz", release=86)

        ok, why = cli.rollback_archive_matches_its_name(p)

        self.assertTrue(ok, why)

    def test_a_file_that_cannot_be_read_is_refused_and_says_why(self):
        p = self.tmp / "pkm-0.2.0-86.igos.tar.gz"
        p.write_bytes(b"not a tarball")

        ok, why = cli.rollback_archive_matches_its_name(p)

        self.assertFalse(ok)
        self.assertTrue(why)

    def test_cache_clean_reports_a_mislabelled_file_and_never_deletes_it(self):
        """Refuse and report. Deleting it silently destroys the evidence."""
        from pkm.database import PackageDB

        rollback = self.tmp / "rollback"
        rollback.mkdir()
        db_root = self.tmp / "dbroot"
        db_root.mkdir()
        db = PackageDB(db_path=str(self.tmp / "pkm.db"), root=str(db_root))
        db.add_installed("pkm", "0.2.0", release=89, tier="core")
        bad = _archive(rollback / "pkm-0.2.0-86.igos.tar.gz", release=73)

        with patch.object(pkm.repo, "REPO_ROLLBACK_DIR", rollback):
            out = io.StringIO()
            with redirect_stdout(out):
                cli._cache_clean_rollback(db)
            printed = out.getvalue()

        self.assertTrue(bad.exists(), "a mislabelled archive was deleted")
        self.assertIn("pkm-0.2.0-86.igos.tar.gz", printed)
        self.assertIn("73", printed)


if __name__ == "__main__":
    unittest.main()


class _Repo:
    def __init__(self, remote):
        self.remote = remote

    def get_package(self, name):
        return self.remote.get(name)

    def download_package(self, name, reporter=None):
        return True, f"/nonexistent/{name}.igos.tar.gz"

    def resolve_dependencies(self, name, db):
        return True, [name]

    def has_synced_index(self):
        return True


class _Installer:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def install(self, name, archive_path=None, expected_sha256=None,
                install_reason="manual", reporter=None, sidecars_out=None,
                queue=None):
        self.calls.append({"name": name, "archive_path": archive_path})
        return self.result


class _Remover:
    def __init__(self, db, root=None):
        self.db = db

    def remove(self, name, force=False, reporter=None, on_file=None,
               run_pre_remove_hook=True, run_post_remove_hook=None,
               keep_helper_payload=False):
        return True, f"Removed {name}"


class TheTransactionSaysWhatItHasToRollBackTo(unittest.TestCase):
    """The upgrade path states rollback coverage per step, and refuses a
    restore it cannot confirm. Drives the real handler with the repository
    and installer stubbed: what is under test is the DECISION, not the
    download or the extraction."""

    def setUp(self):
        import argparse
        from pkm.database import PackageDB
        self._argparse = argparse
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.tmp / "root"))
        self.addCleanup(self.db.close)
        self.db.add_installed("kern", "1.0", tier="core")
        self.remote = {
            "kern": {"name": "kern", "version": "2.0", "release": 1,
                     "sha256": "0" * 64, "size": 10, "depends": []},
        }

    def _run(self, installer, rollback_saved):
        from pkm import output
        args = self._argparse.Namespace(
            packages=["kern"], upgrade_all=False, allow_downgrade=False,
            ignore_holds=False, upgrade_security_only=False,
            upgrade_allow_kernel_replace=True, assume_yes=True,
            upgrade_dry_run=False, quiet=False, verbose=False,
        )
        buf = io.StringIO()
        prior = output.process_level()
        output._process_reporter.stream = buf
        output._process_reporter.err_stream = buf
        try:
            with redirect_stdout(buf), \
                 patch.object(cli, "RepoManager",
                              lambda *a, **k: _Repo(self.remote)), \
                 patch.object(cli, "PackageInstaller",
                              lambda *a, **k: installer), \
                 patch("pkm.remover.PackageRemover", _Remover), \
                 patch.object(cli, "_confirm_upgrade", lambda _a: True), \
                 patch.object(cli, "_save_rollback_archive",
                              lambda *a, **k: rollback_saved), \
                 patch.object(cli,
                              "refresh_available_updates_after_transaction",
                              lambda db, **k: None), \
                 patch("pkm.pretxn.run_pre_transaction_hook",
                       lambda *a, **k: None):
                cli.cmd_upgrade(self.db, args)
        finally:
            output.set_process_level(prior)
            output._process_reporter.stream = None
            output._process_reporter.err_stream = None
        return " ".join(buf.getvalue().split())

    def test_no_pre_upgrade_copy_is_stated_on_the_step_it_applies_to(self):
        """Writing nothing is right; writing nothing SILENTLY is the same
        mistake in a quieter voice."""
        text = self._run(_Installer((True, "Installed kern 2.0")), None)

        self.assertIn("no pre-upgrade copy of kern", text)
        self.assertIn("rollback for this step relies on the chronicle "
                      "restore point", text)

    def test_a_mislabelled_rollback_file_is_not_restored_from(self):
        """The upgrade has already failed. This is the worst moment to put a
        release nobody chose onto the machine."""
        bad = _archive(self.tmp / "kern-1.0-1.igos.tar.gz",
                       name="kern", version="1.0", release=7)
        installer = _Installer((False, "install failed"))

        text = self._run(installer, (bad, "0" * 64))

        self.assertIn("NOT restoring kern from the rollback cache", text)
        self.assertIn("carries release 7", text)
        restore_calls = [c for c in installer.calls
                         if c["archive_path"] == str(bad)]
        self.assertEqual(
            restore_calls, [],
            "a rollback archive whose release contradicts its name was "
            "installed anyway",
        )

    def test_a_matching_rollback_file_is_still_restored_from(self):
        """The gate refuses the wrong file, not every file."""
        good = _archive(self.tmp / "kern-1.0-1.igos.tar.gz",
                        name="kern", version="1.0", release=1)
        installer = _Installer((False, "install failed"))

        text = self._run(installer, (good, "0" * 64))

        self.assertNotIn("NOT restoring", text)
        restore_calls = [c for c in installer.calls
                         if c["archive_path"] == str(good)]
        self.assertEqual(len(restore_calls), 1, text)
