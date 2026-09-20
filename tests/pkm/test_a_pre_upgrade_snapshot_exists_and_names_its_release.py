#!/usr/bin/env python3
"""An upgrade leaves a restorable copy of the build it replaced.

`pkm cache --help` states that each `pkm upgrade` writes a pre-upgrade
snapshot so a failed install can be reverted. Measured on an installed machine
2026-09-19: `pkm upgrade forge --yes` (1.0.0-241 to 1.0.0-245) exited 0, printed
"no pre-upgrade copy of forge 1.0.0-241 was available", and left
/var/cache/pkm/rollback/ unchanged. The live bytes said why:
/var/cache/pkm/packages/forge-1.0.0.igos.tar.gz carried pkgrel=241 before the
command and pkgrel=245 after it.

Two causes, both in this file's scope.

ORDER. The upgrade path downloaded the replacement and only then looked for a
copy of the outgoing release. By then the download had already written over it.

NAME. The archive is cached under the filename the signed index publishes, and
the published filenames carry no release, so every release of a version lands
on the same path. Two releases cannot coexist in the cache, and the download
also unlinks that path when the checksum does not match — so the outgoing
build's only local copy is destroyed either way. The cache's own cleaner already
expects the release-qualified shape and declines to parse a release-less one,
leaving those files untouched -- which is correct, and has to keep holding for
every machine that upgraded before this change.

The rule these tests fix: the snapshot is taken BEFORE anything can overwrite
its source; a cached archive is named for the exact build it holds so two
releases coexist; the default cache policy keeps the previous release, which is
the one a rollback needs; and no code invents a package name out of a filename
it cannot parse.
"""

import argparse
import io
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pkm.repo
from pkm import cli
from pkm.database import PackageDB


def _archive(path, name, version, release):
    """A minimal .igos.tar.gz whose ./.PKGINFO names this exact build."""
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
        payload = f"payload for {name} {version}-{release}".encode()
        info = tarfile.TarInfo("./usr/bin/stub")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return path


def _pkgrel_of(path):
    """The release the archive's own .PKGINFO claims."""
    with tarfile.open(path) as tf:
        for member in ("./.PKGINFO", ".PKGINFO"):
            try:
                text = tf.extractfile(member).read().decode()
            except KeyError:
                continue
            for line in text.splitlines():
                if line.startswith("pkgrel="):
                    return int(line.split("=", 1)[1])
    raise AssertionError(f"{path} carries no pkgrel")


# ── The cache holds one file per BUILD, not one per version ───────────────

class TheDownloadCacheKeepsTheBuildItNames(unittest.TestCase):
    """The packages cache is where a rollback's source comes from."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.pkg_cache = self.tmp / "packages"
        self.pkg_cache.mkdir()
        p = patch.object(pkm.repo, "REPO_PKG_CACHE", self.pkg_cache)
        p.start()
        self.addCleanup(p.stop)

    def _repo(self, remote):
        repo = pkm.repo.RepoManager.__new__(pkm.repo.RepoManager)
        repo.root = None
        repo.get_package = lambda n: remote.get(n)
        repo._mirror_urls_for_pkg = lambda pkg, filename: [
            f"https://example.invalid/{filename}"
        ]

        def _honest_checksum(path, expected):
            # Stands in for sha256-against-the-signed-index: it matches one
            # exact build. A stub that always agrees would let a cached file
            # of the wrong release pass as a cache hit, which is the failure
            # this class exists to detect.
            try:
                return _pkgrel_of(Path(path)) == remote["forge"]["release"]
            except Exception:
                return False

        repo._verify_checksum = _honest_checksum
        return repo

    def test_downloading_a_new_release_does_not_destroy_the_old_one(self):
        """The measured shape: the index publishes a release-less filename.

        forge 1.0.0-241 is cached. The mirror serves 1.0.0-245 under the same
        published name. Afterwards a copy of 241 must still exist locally,
        because it is the only thing a rollback can restore from.
        """
        old = _archive(self.pkg_cache / "forge-1.0.0-241.igos.tar.gz",
                       "forge", "1.0.0", 241)
        # The legacy release-less shape is what the cache actually holds on a
        # machine that has upgraded before this change.
        legacy = _archive(self.pkg_cache / "forge-1.0.0.igos.tar.gz",
                          "forge", "1.0.0", 241)
        remote = {"forge": {"name": "forge", "version": "1.0.0", "release": 245,
                            "filename": "forge-1.0.0.igos.tar.gz",
                            "sha256": "0" * 64, "size": 10}}
        repo = self._repo(remote)

        def _fake_download(url, dest, on_bytes=None, **kw):
            _archive(Path(dest), "forge", "1.0.0", 245)

        repo._download = _fake_download
        ok, result = repo.download_package("forge")
        self.assertTrue(ok, result)

        self.assertTrue(
            old.exists(),
            "the cached archive of the release being replaced was destroyed "
            "by the download of its replacement",
        )
        self.assertEqual(
            _pkgrel_of(old), 241,
            "the cached archive named -241 no longer carries release 241",
        )
        self.assertEqual(
            _pkgrel_of(Path(result)), 245,
            "the downloaded path does not hold the release that was fetched",
        )
        self.assertNotEqual(
            Path(result).resolve(), old.resolve(),
            "the new release landed on the old release's file",
        )
        self.assertEqual(
            _pkgrel_of(legacy), 241,
            "a pre-existing release-less cache file was overwritten in place, "
            "which is how the only copy of the outgoing build is lost",
        )

    def test_the_cached_file_is_named_for_the_build_it_holds(self):
        """A filename is a claim; it has to be one the bytes support."""
        remote = {"forge": {"name": "forge", "version": "1.0.0", "release": 245,
                            "filename": "forge-1.0.0.igos.tar.gz",
                            "sha256": "0" * 64, "size": 10}}
        repo = self._repo(remote)
        repo._download = lambda url, dest, on_bytes=None, **kw: _archive(
            Path(dest), "forge", "1.0.0", 245)

        ok, result = repo.download_package("forge")
        self.assertTrue(ok, result)
        self.assertEqual(
            Path(result).name, "forge-1.0.0-245.igos.tar.gz",
            "the archive is cached under a name that does not state its "
            "release, so no two releases of a version can coexist",
        )


# ── The snapshot is taken before anything can overwrite its source ────────

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


class AnUpgradeLeavesSomethingToRollBackTo(unittest.TestCase):
    """The real upgrade handler, with a download that behaves like the real
    one: it writes the incoming archive into the packages cache."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.pkg_cache = self.tmp / "packages"
        self.rollback = self.tmp / "rollback"
        self.pkg_cache.mkdir()
        self.rollback.mkdir()
        for attr, value in (("REPO_PKG_CACHE", self.pkg_cache),
                            ("REPO_ROLLBACK_DIR", self.rollback)):
            p = patch.object(pkm.repo, attr, value)
            p.start()
            self.addCleanup(p.stop)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.tmp / "root"))
        self.addCleanup(self.db.close)
        self.db.add_installed("forge", "1.0.0", release=241, tier="core")
        self.order = []

    def _run(self, installer, download):
        from pkm import output

        order = self.order
        real_save = cli._save_rollback_archive

        def recording_save(*a, **k):
            order.append("snapshot")
            return real_save(*a, **k)

        class _Repo:
            def __init__(self, *a, **k):
                pass

            def get_package(self, name):
                return {"name": "forge", "version": "1.0.0", "release": 245,
                        "filename": "forge-1.0.0.igos.tar.gz",
                        "sha256": "0" * 64, "size": 10, "depends": []}

            def download_package(self, name, reporter=None):
                order.append("download")
                return download()

            def resolve_dependencies(self, name, db):
                return True, [name]

            def has_synced_index(self):
                return True

        args = argparse.Namespace(
            packages=["forge"], upgrade_all=False, allow_downgrade=False,
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
                 patch.object(cli, "RepoManager", _Repo), \
                 patch.object(cli, "PackageInstaller",
                              lambda *a, **k: installer), \
                 patch("pkm.remover.PackageRemover", _Remover), \
                 patch.object(cli, "_confirm_upgrade", lambda _a: True), \
                 patch.object(cli, "_save_rollback_archive", recording_save), \
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

    def _overwriting_download(self):
        """What the real download does to the cache on a mirror upgrade."""
        def _dl():
            dest = self.pkg_cache / "forge-1.0.0.igos.tar.gz"
            _archive(dest, "forge", "1.0.0", 245)
            return True, str(dest)
        return _dl

    def test_the_upgrade_writes_a_snapshot_of_the_release_it_replaces(self):
        """THE MEASURED CASE. The cache holds 241 under the release-less name
        the index publishes, and the download replaces those bytes with 245."""
        _archive(self.pkg_cache / "forge-1.0.0.igos.tar.gz",
                 "forge", "1.0.0", 241)

        text = self._run(_Installer((True, "Installed forge 1.0.0-245")),
                         self._overwriting_download())

        saved = sorted(self.rollback.glob("*.igos.tar.gz"))
        self.assertEqual(
            [p.name for p in saved], ["forge-1.0.0-241.igos.tar.gz"],
            f"no pre-upgrade snapshot was written; the transaction said: {text}",
        )
        self.assertEqual(
            _pkgrel_of(saved[0]), 241,
            "the snapshot does not carry the release its name claims",
        )

    def test_the_snapshot_is_taken_before_the_download(self):
        """Ordering is the control. Anything after the download is racing it."""
        _archive(self.pkg_cache / "forge-1.0.0.igos.tar.gz",
                 "forge", "1.0.0", 241)

        self._run(_Installer((True, "Installed forge 1.0.0-245")),
                  self._overwriting_download())

        self.assertIn("snapshot", self.order, "no snapshot step ran at all")
        self.assertIn("download", self.order, "no download step ran at all")
        self.assertLess(
            self.order.index("snapshot"), self.order.index("download"),
            f"the snapshot was taken after the download: {self.order}",
        )

    def test_with_no_local_copy_at_all_the_step_still_says_so(self):
        """The honest line stays for the case it was written for."""
        text = self._run(_Installer((True, "Installed forge 1.0.0-245")),
                         self._overwriting_download())

        self.assertEqual(
            sorted(self.rollback.glob("*.igos.tar.gz")), [],
            "a snapshot was written although nothing local carried 241",
        )
        self.assertIn("no pre-upgrade copy of forge", text)


# ── The cleaner keeps what a rollback needs, and invents nothing ──────────

class TheCacheCleanerKeepsThePreviousRelease(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.pkg_cache = self.tmp / "packages"
        self.pkg_cache.mkdir()
        p = patch.object(pkm.repo, "REPO_PKG_CACHE", self.pkg_cache)
        p.start()
        self.addCleanup(p.stop)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.tmp / "root"))
        self.addCleanup(self.db.close)
        self.db.add_installed("forge", "1.0.0", release=245, tier="core")

    def _clean(self, **kw):
        from pkm import output
        args = argparse.Namespace(
            cache_action="clean", cache_keep_current=True, cache_keep_n=None,
            cache_all=False, cache_rollback=False, quiet=False, verbose=False,
            **kw
        )
        buf = io.StringIO()
        prior = output.process_level()
        output._process_reporter.stream = buf
        output._process_reporter.err_stream = buf
        try:
            with redirect_stdout(buf):
                cli.cmd_cache(self.db, args)
        finally:
            output.set_process_level(prior)
            output._process_reporter.stream = None
            output._process_reporter.err_stream = None
        return " ".join(buf.getvalue().split())

    def test_the_previous_release_survives_a_default_clean(self):
        """The archive a rollback restores from is the previous release.

        Keeping only the installed one removes the safety net the help text
        promises on the very next upgrade.
        """
        for rel in (240, 241, 245):
            _archive(self.pkg_cache / f"forge-1.0.0-{rel}.igos.tar.gz",
                     "forge", "1.0.0", rel)

        self._clean()

        left = sorted(p.name for p in self.pkg_cache.glob("*.igos.tar.gz"))
        self.assertIn("forge-1.0.0-245.igos.tar.gz", left,
                      "the installed release was removed")
        self.assertIn(
            "forge-1.0.0-241.igos.tar.gz", left,
            "the previous release was removed, so the next upgrade has "
            f"nothing to snapshot; cache now holds {left}",
        )
        self.assertNotIn("forge-1.0.0-240.igos.tar.gz", left,
                         "an older release was kept; the policy is exactly one")

    def test_a_release_less_archive_is_not_read_as_another_package(self):
        """A guard, not a defect: this already holds and must keep holding.

        `forge-1.0.0.igos.tar.gz` is forge. The cleaner's filename pattern
        requires two hyphens and a numeric tail, which a release-less name
        does not have, so the file is reported as unparseable and left alone
        rather than attributed to an invented package. Release-qualified
        caching removes the shape going forward; every machine that upgraded
        before it still has these files, and the cleaner must not start
        deleting them on the strength of a filename guess.
        """
        legacy = _archive(self.pkg_cache / "forge-1.0.0.igos.tar.gz",
                          "forge", "1.0.0", 241)
        _archive(self.pkg_cache / "forge-1.0.0-245.igos.tar.gz",
                 "forge", "1.0.0", 245)

        text = self._clean()

        self.assertNotIn(
            "forge-1 ", text,
            f"a package name was invented from a filename: {text}",
        )
        self.assertTrue(
            legacy.exists(),
            "a legacy release-less archive was removed on the strength of a "
            "filename parse that got its package wrong",
        )


if __name__ == "__main__":
    unittest.main()
