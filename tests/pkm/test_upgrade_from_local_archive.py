#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`pkm upgrade <name> --archive <file>` moves an INSTALLED package forward
from a local archive.

Until this landed, a package built ahead of the mirror had no honest path onto
a machine that already carried it: `pkm install --archive` refuses an installed
package, and `pkm upgrade` / `pkm reinstall` resolve only from the signed
index. The pre-mint installed-gate record (a freshly built assistant archive
onto a real R001.x machine) was blocked on exactly that on 2026-08-27, and the
only way through was remove-then-install, which throws away the downgrade
guard, the restore point and the rollback copy the upgrade path carries.

Asserted here:

  1. the upgrade command accepts --archive and --archive-trust;
  2. the named package is replaced FROM THE GIVEN FILE, with the file's own
     sha256 threaded into the install-time re-hash gate, through the same
     remove / install / rollback sequence the repository path uses, and the
     history row says so;
  3. the identity checks refuse before anything is touched: a package that is
     not installed, an archive whose .PKGINFO names another package, an
     archive with no metadata, a corrupt file, more than one name, --all,
     --security-only, a held package;
  4. the direction is checked the way the repository path checks it: an older
     archive refuses and names both numbers, --allow-downgrade permits it and
     says so, the same build is nothing to do;
  5. the trust gate is the one `pkm install --archive` applies: strict (the
     default) and repo-only need the index to carry this exact archive, loose
     proceeds with a warning;
  6. a runtime dependency the archive declares that is not installed refuses
     (this command fetches nothing from the repository);
  7. --dry-run prints the plan, naming the local archive, and changes nothing;
  8. when the package being replaced is the package manager itself, every one
     of its own modules is loaded BEFORE the first file is replaced, so the
     transaction finishes on the code it started with;
  9. the real installer and remover, on a scratch root, replace the files and
     the database row and record the transition.
"""

import argparse
import hashlib
import io
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pkm.cli as cli
from pkm import output
from pkm.database import PackageDB
from pkm.installer import PackageInstaller
from pkm.remover import PackageRemover

NAME = "widget"


def _make_archive(path: Path, name, version, release, files, depends=()):
    """A minimal .igos.tar.gz: a .PKGINFO plus the given payload files."""
    pkginfo = (f"pkgname = {name}\npkgver = {version}\npkgrel = {release}\n"
               f"pkgdesc = test package\n"
               + "".join(f"depend = {d}\n" for d in depends))
    with tarfile.open(path, "w:gz") as tf:
        data = pkginfo.encode()
        info = tarfile.TarInfo(".PKGINFO")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        for rel, content in files.items():
            info = tarfile.TarInfo(rel)
            info.size = len(content)
            info.mode = 0o755 if rel.startswith("usr/bin/") else 0o644
            tf.addfile(info, io.BytesIO(content))
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _flat(text: str) -> str:
    """The reporter wraps long lines at the terminal width; assertions on a
    phrase compare against the whitespace-flattened text."""
    return " ".join(text.split())


class FakeRepo:
    def __init__(self, remote=None):
        self.remote = remote or {}
        self.downloads = []

    def get_package(self, name):
        return self.remote.get(name)

    def download_package(self, name, reporter=None):
        self.downloads.append(name)
        return True, f"/tmp/{name}.igos.tar.gz"

    def resolve_dependencies(self, name, db):
        return True, [name]


class FakeInstaller:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def install(self, name, archive_path=None, expected_sha256=None,
                install_reason="manual", reporter=None, sidecars_out=None,
                queue=None):
        self.calls.append({"name": name, "archive_path": archive_path,
                           "expected_sha256": expected_sha256,
                           "install_reason": install_reason})
        if not self.ok:
            return False, "deploy failed (test)"
        return True, "Installed"

    def reattach_helper_payload(self, name):
        return True, ""


class FakeRemover:
    calls = []
    modules_loaded_at_call = None

    def __init__(self, db, root=None):
        self.db = db

    def remove(self, name, force=False, reporter=None, on_file=None,
               run_pre_remove_hook=True, run_post_remove_hook=None,
               keep_helper_payload=False):
        FakeRemover.calls.append(name)
        FakeRemover.modules_loaded_at_call = {
            m for m in sys.modules if m.startswith("pkm.")}
        return True, f"Removed {name}"


class _Base(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))
        # The installed build carries its file AND the file's recorded
        # sha256, which is what a real install leaves behind — and what the
        # same-release comparison reads. Registering a name with no file
        # rows leaves a build whose content is unrecorded, which is a
        # different case with its own tests (see
        # tests/pkm/test_archive_at_the_same_release_is_compared_not_assumed.py).
        _pkg_id = self.db.add_installed(NAME, "1.0", release=3, tier="core")
        _installed = self.root / "usr" / "bin" / "widget"
        _installed.parent.mkdir(parents=True, exist_ok=True)
        _installed.write_bytes(b"three")
        self.db.add_files(_pkg_id, ["usr/bin/widget"])
        self.archive = _make_archive(
            self.tmp / f"{NAME}-1.0-4.igos.tar.gz", NAME, "1.0", 4,
            {"usr/bin/widget": b"#!/bin/sh\necho four\n"})
        FakeRemover.calls = []
        FakeRemover.modules_loaded_at_call = None

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _args(self, archive, **over):
        base = dict(
            packages=[NAME], upgrade_all=False, allow_downgrade=False,
            ignore_holds=False, upgrade_security_only=False,
            upgrade_allow_kernel_replace=False, upgrade_yes=True,
            upgrade_dry_run=False, quiet=False, verbose=False,
            archive=str(archive) if archive is not None else None,
            archive_trust="loose",
        )
        base.update(over)
        return argparse.Namespace(**base)

    def _run(self, args, installer=None, repo=None):
        installer = installer or FakeInstaller()
        repo = repo or FakeRepo()
        buf = io.StringIO()
        prior = output.process_level()
        output.set_process_level(output.NORMAL)
        output._process_reporter.stream = buf
        output._process_reporter.err_stream = buf
        rc = None
        try:
            with redirect_stdout(buf), \
                 patch.object(cli, "RepoManager", lambda *a, **k: repo), \
                 patch.object(cli, "PackageInstaller", lambda *a, **k: installer), \
                 patch("pkm.remover.PackageRemover", FakeRemover), \
                 patch.object(cli, "_save_rollback_archive", lambda *a, **k: None), \
                 patch.object(cli, "refresh_available_updates_after_transaction", lambda db, **k: None), \
                 patch.object(cli, "_print_transaction_next_steps", lambda *a, **k: None), \
                 patch.object(cli, "helper_is_present", lambda n: False), \
                 patch.object(cli, "helper_payload_present", lambda n: False), \
                 patch("pkm.pretxn.run_pre_transaction_hook", lambda *a, **k: None):
                try:
                    rc = cli.cmd_upgrade(self.db, args)
                except SystemExit as e:
                    rc = e.code
        finally:
            output.set_process_level(prior)
            output._process_reporter.stream = None
            output._process_reporter.err_stream = None
        return rc, buf.getvalue(), installer, repo


class TheParserAcceptsTheOption(unittest.TestCase):

    def test_upgrade_takes_archive_and_trust(self):
        args = cli.build_parser().parse_args(
            ["upgrade", NAME, "--archive", "/x/widget.igos.tar.gz",
             "--archive-trust", "loose"])
        self.assertEqual(args.archive, "/x/widget.igos.tar.gz")
        self.assertEqual(args.archive_trust, "loose")

    def test_the_trust_default_is_strict(self):
        args = cli.build_parser().parse_args(
            ["upgrade", NAME, "--archive", "/x/widget.igos.tar.gz"])
        self.assertEqual(args.archive_trust, "strict")


class TheInstalledPackageMovesForwardFromTheFile(_Base):

    def test_the_given_file_is_installed_with_its_own_hash(self):
        rc, out, installer, repo = self._run(self._args(self.archive))
        self.assertEqual(rc, 0, out)
        self.assertEqual(FakeRemover.calls, [NAME])
        self.assertEqual(len(installer.calls), 1, out)
        call = installer.calls[0]
        self.assertEqual(call["archive_path"], str(self.archive))
        self.assertEqual(call["expected_sha256"], _sha(self.archive))
        self.assertEqual(repo.downloads, [], "a local archive is never downloaded")
        self.assertIn("Upgraded widget", out)
        self.assertIn("1.0-3 -> 1.0-4", out)

    def test_the_history_row_says_the_source_was_a_local_archive(self):
        rc, out, *_ = self._run(self._args(self.archive))
        self.assertEqual(rc, 0, out)
        rows = [h for h in self.db.get_history(limit=10)
                if h["operation"] == "upgrade" and h["package_name"] == NAME]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["method"], "local-archive")

    def test_a_failed_install_fails_the_transaction(self):
        rc, out, *_ = self._run(self._args(self.archive),
                                installer=FakeInstaller(ok=False))
        self.assertEqual(rc, 1, out)
        self.assertIn("did not upgrade: widget", _flat(out))


class TheIdentityChecksRefuseBeforeAnythingIsTouched(_Base):

    def _refused(self, args, fragment, repo=None):
        rc, out, installer, _ = self._run(args, repo=repo)
        self.assertEqual(rc, 1, out)
        self.assertIn(fragment, _flat(out))
        self.assertEqual(FakeRemover.calls, [], "something was removed")
        self.assertEqual(installer.calls, [], "something was installed")
        return out

    def test_a_package_that_is_not_installed(self):
        self.db.remove_installed(NAME)
        out = self._refused(self._args(self.archive), "is not installed")
        self.assertIn(f"pkm install {NAME} --archive", _flat(out))

    def test_an_archive_that_names_another_package(self):
        other = _make_archive(self.tmp / "gadget-2.0-1.igos.tar.gz",
                              "gadget", "2.0", 1, {"usr/bin/gadget": b"x"})
        out = self._refused(self._args(other), "names the package gadget")
        self.assertIn(NAME, out)

    def test_an_archive_without_metadata(self):
        bare = self.tmp / "bare.igos.tar.gz"
        with tarfile.open(bare, "w:gz") as tf:
            info = tarfile.TarInfo("usr/bin/widget")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
        self._refused(self._args(bare), "no .PKGINFO")

    def test_a_corrupt_file(self):
        junk = self.tmp / "junk.igos.tar.gz"
        junk.write_bytes(b"\x1f\x8b" + b"not a tar" * 40)
        self._refused(self._args(junk), "cannot read")

    def test_a_missing_file(self):
        self._refused(self._args(self.tmp / "absent.igos.tar.gz"), "cannot read")

    def test_more_than_one_name(self):
        self._refused(self._args(self.archive, packages=[NAME, "other"]),
                      "exactly one package")

    def test_all_cannot_be_combined_with_a_file(self):
        self._refused(self._args(self.archive, packages=[], upgrade_all=True),
                      "--all")

    def test_security_only_cannot_be_combined_with_a_file(self):
        self._refused(self._args(self.archive, upgrade_security_only=True),
                      "--security-only")

    def test_a_held_package_refuses_unless_told_otherwise(self):
        self.db.set_held(NAME, held=True)
        self._refused(self._args(self.archive), "is held")
        rc, out, installer, _ = self._run(self._args(self.archive,
                                                     ignore_holds=True))
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(installer.calls), 1)


class TheDirectionIsChecked(_Base):

    def test_an_older_archive_refuses_and_names_both_numbers(self):
        older = _make_archive(self.tmp / f"{NAME}-1.0-2.igos.tar.gz", NAME,
                              "1.0", 2, {"usr/bin/widget": b"two"})
        rc, out, installer, _ = self._run(self._args(older))
        self.assertEqual(rc, 1, out)
        self.assertIn("refusing to downgrade widget 1.0-3 -> 1.0-2", _flat(out))
        self.assertIn("--allow-downgrade", out)
        self.assertEqual(installer.calls, [])

    def test_the_override_permits_it_and_says_so(self):
        older = _make_archive(self.tmp / f"{NAME}-1.0-2.igos.tar.gz", NAME,
                              "1.0", 2, {"usr/bin/widget": b"two"})
        rc, out, installer, _ = self._run(self._args(older, allow_downgrade=True))
        self.assertEqual(rc, 0, out)
        self.assertIn("DOWNGRADING widget 1.0-3 -> 1.0-2", _flat(out))
        self.assertEqual(installer.calls[0]["archive_path"], str(older))

    def test_the_same_build_is_nothing_to_do(self):
        # The archive holds the same bytes the installed build recorded, so
        # it IS the same build — which is now checked rather than inferred
        # from the version and release matching.
        same = _make_archive(self.tmp / f"{NAME}-1.0-3.igos.tar.gz", NAME,
                             "1.0", 3, {"usr/bin/widget": b"three"})
        rc, out, installer, _ = self._run(self._args(same))
        self.assertEqual(rc, 0, out)
        self.assertIn("already installed", out)
        self.assertIn("nothing was changed", _flat(out))
        self.assertEqual(installer.calls, [])
        self.assertEqual(FakeRemover.calls, [])


class TheTrustGateIsTheInstallOne(_Base):

    def test_strict_refuses_an_archive_the_index_does_not_carry(self):
        rc, out, installer, _ = self._run(self._args(self.archive,
                                                     archive_trust="strict"))
        self.assertEqual(rc, 1, out)
        self.assertIn("--archive-trust=strict requires", _flat(out))
        self.assertEqual(installer.calls, [])

    def test_repo_only_refuses_the_same_way(self):
        rc, out, installer, _ = self._run(self._args(self.archive,
                                                     archive_trust="repo-only"))
        self.assertEqual(rc, 1, out)
        self.assertIn("--archive-trust=repo-only requires", _flat(out))
        self.assertEqual(installer.calls, [])

    def test_strict_proceeds_when_the_index_carries_this_exact_archive(self):
        repo = FakeRepo({NAME: {"name": NAME, "version": "1.0", "release": 4,
                                "sha256": _sha(self.archive), "size": 10,
                                "depends": []}})
        rc, out, installer, _ = self._run(self._args(self.archive,
                                                     archive_trust="strict"),
                                          repo=repo)
        self.assertEqual(rc, 0, out)
        self.assertIn("matches the repository index", _flat(out))
        self.assertEqual(len(installer.calls), 1)

    def test_loose_proceeds_with_the_warning(self):
        rc, out, installer, _ = self._run(self._args(self.archive,
                                                     archive_trust="loose"))
        self.assertEqual(rc, 0, out)
        self.assertIn("--archive-trust=loose", out)
        self.assertIn("Verify SHA256 independently", _flat(out))
        self.assertEqual(len(installer.calls), 1)

    def test_a_mismatching_index_entry_is_named(self):
        repo = FakeRepo({NAME: {"name": NAME, "version": "1.0", "release": 3,
                                "sha256": "0" * 64, "size": 10, "depends": []}})
        rc, out, *_ = self._run(self._args(self.archive, archive_trust="loose"),
                                repo=repo)
        self.assertEqual(rc, 0, out)
        self.assertIn("does not match", out)


class ADeclaredDependencyThatIsNotInstalledRefuses(_Base):

    def test_the_missing_dependency_is_named(self):
        needy = _make_archive(self.tmp / f"{NAME}-1.0-4.igos.tar.gz", NAME,
                              "1.0", 4, {"usr/bin/widget": b"four"},
                              depends=("libgizmo", "libthing"))
        self.db.add_installed("libthing", "2.0", release=1, tier="core")
        rc, out, installer, repo = self._run(self._args(needy))
        self.assertEqual(rc, 1, out)
        self.assertIn("libgizmo", out)
        self.assertNotIn("libthing", _flat(out).split("not installed")[1][:200])
        self.assertIn("sudo pkm install libgizmo", _flat(out))
        self.assertEqual(installer.calls, [])
        self.assertEqual(repo.downloads, [])

    def test_installed_dependencies_are_fine(self):
        fine = _make_archive(self.tmp / f"{NAME}-1.0-4.igos.tar.gz", NAME,
                             "1.0", 4, {"usr/bin/widget": b"four"},
                             depends=("libthing",))
        self.db.add_installed("libthing", "2.0", release=1, tier="core")
        rc, out, installer, _ = self._run(self._args(fine))
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(installer.calls), 1)


class DryRunChangesNothing(_Base):

    def test_the_plan_names_the_local_archive(self):
        rc, out, installer, _ = self._run(self._args(self.archive,
                                                     upgrade_dry_run=True))
        self.assertEqual(rc, 0, out)
        self.assertIn("Upgrade plan: 1 package(s)", out)
        self.assertIn("1.0-3", out)
        self.assertIn("1.0-4", out)
        self.assertIn("Local archive", out)
        self.assertIn(str(self.archive), out)
        self.assertNotIn("Download size", out)
        self.assertIn("--dry-run", out)
        self.assertEqual(installer.calls, [])
        self.assertEqual(FakeRemover.calls, [])


class ThePackageManagerReplacingItself(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.tmp / "root"))
        self.db.add_installed("pkm", "0.2.0", release=73, tier="core")
        self.archive = _make_archive(
            self.tmp / "pkm-0.2.0-76.igos.tar.gz", "pkm", "0.2.0", 76,
            {"usr/lib/python3.13/site-packages/pkm/cli.py": b"# new\n"})
        FakeRemover.calls = []
        FakeRemover.modules_loaded_at_call = None

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def test_every_own_module_is_loaded_before_the_first_file_moves(self):
        import pkgutil
        import pkm as pkm_package
        expected = {
            "pkm." + m.name for m in pkgutil.iter_modules(pkm_package.__path__)
            if m.name != "__main__"}
        args = argparse.Namespace(
            packages=["pkm"], upgrade_all=False, allow_downgrade=False,
            ignore_holds=False, upgrade_security_only=False,
            upgrade_allow_kernel_replace=False, upgrade_yes=True,
            upgrade_dry_run=False, quiet=False, verbose=False,
            archive=str(self.archive), archive_trust="loose")
        installer = FakeInstaller()
        buf = io.StringIO()
        prior = output.process_level()
        output.set_process_level(output.NORMAL)
        output._process_reporter.stream = buf
        output._process_reporter.err_stream = buf
        try:
            with redirect_stdout(buf), \
                 patch.object(cli, "RepoManager", lambda *a, **k: FakeRepo()), \
                 patch.object(cli, "PackageInstaller", lambda *a, **k: installer), \
                 patch("pkm.remover.PackageRemover", FakeRemover), \
                 patch.object(cli, "_save_rollback_archive", lambda *a, **k: None), \
                 patch.object(cli, "refresh_available_updates_after_transaction", lambda db, **k: None), \
                 patch.object(cli, "_print_transaction_next_steps", lambda *a, **k: None), \
                 patch.object(cli, "helper_is_present", lambda n: False), \
                 patch.object(cli, "helper_payload_present", lambda n: False), \
                 patch("pkm.pretxn.run_pre_transaction_hook", lambda *a, **k: None):
                rc = cli.cmd_upgrade(self.db, args)
        finally:
            output.set_process_level(prior)
            output._process_reporter.stream = None
            output._process_reporter.err_stream = None
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        self.assertEqual(FakeRemover.calls, ["pkm"])
        missing = expected - (FakeRemover.modules_loaded_at_call or set())
        self.assertEqual(missing, set(),
                         f"own modules not loaded before the replacement: {sorted(missing)}")
        self.assertIn("loaded before", _flat(out))


class TheRealInstallerAndRemoverOnAScratchRoot(unittest.TestCase):
    """End to end with the real collaborators: the first build lands through
    the installer's explicit-archive path, the second through the command."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        (self.root / "usr/bin").mkdir(parents=True)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))
        self.installer = PackageInstaller(self.db, root=str(self.root))
        self.old = _make_archive(self.tmp / f"{NAME}-1.0-3.igos.tar.gz", NAME,
                                 "1.0", 3, {"usr/bin/widget": b"three\n",
                                            "usr/bin/widget-old": b"gone\n"})
        self.new = _make_archive(self.tmp / f"{NAME}-1.0-4.igos.tar.gz", NAME,
                                 "1.0", 4, {"usr/bin/widget": b"four\n",
                                            "usr/bin/widget-new": b"new\n"})

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def test_the_files_and_the_row_move_forward(self):
        ok, msg = self.installer.install(NAME, archive_path=str(self.old),
                                         expected_sha256=_sha(self.old))
        self.assertTrue(ok, msg)
        self.assertEqual((self.root / "usr/bin/widget").read_bytes(), b"three\n")
        args = argparse.Namespace(
            packages=[NAME], upgrade_all=False, allow_downgrade=False,
            ignore_holds=False, upgrade_security_only=False,
            upgrade_allow_kernel_replace=False, upgrade_yes=True,
            upgrade_dry_run=False, quiet=False, verbose=False,
            archive=str(self.new), archive_trust="loose")
        buf = io.StringIO()
        prior = output.process_level()
        output.set_process_level(output.NORMAL)
        output._process_reporter.stream = buf
        output._process_reporter.err_stream = buf
        try:
            with redirect_stdout(buf), \
                 patch.object(cli, "RepoManager", lambda *a, **k: FakeRepo()), \
                 patch.object(cli, "PackageInstaller", lambda *a, **k: self.installer), \
                 patch("pkm.remover.PackageRemover",
                       lambda db, root=None: PackageRemover(db, root=str(self.root))), \
                 patch.object(cli, "_save_rollback_archive", lambda *a, **k: None), \
                 patch.object(cli, "refresh_available_updates_after_transaction", lambda db, **k: None), \
                 patch.object(cli, "_print_transaction_next_steps", lambda *a, **k: None), \
                 patch.object(cli, "helper_is_present", lambda n: False), \
                 patch.object(cli, "helper_payload_present", lambda n: False), \
                 patch("pkm.pretxn.run_pre_transaction_hook", lambda *a, **k: None):
                rc = cli.cmd_upgrade(self.db, args)
        finally:
            output.set_process_level(prior)
            output._process_reporter.stream = None
            output._process_reporter.err_stream = None
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.root / "usr/bin/widget").read_bytes(), b"four\n")
        self.assertTrue((self.root / "usr/bin/widget-new").exists())
        self.assertFalse((self.root / "usr/bin/widget-old").exists(),
                         "the outgoing build's file survived the replacement")
        row = self.db.get_installed(NAME)
        self.assertEqual((row["version"], int(row["release"])), ("1.0", 4))
        self.assertIn("Upgraded widget", out)


class TheVersionComesFromTheArchiveMetadata(unittest.TestCase):
    """Found by the end-to-end test above: the release was read from .PKGINFO
    while the version still came from the file name. The rollback cache saves
    an archive as <name>-<version>-<release>.igos.tar.gz, so a restore from it
    registered the version as "1.0-3" beside release 3."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        (self.root / "usr/bin").mkdir(parents=True)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))
        self.installer = PackageInstaller(self.db, root=str(self.root))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def test_a_release_qualified_file_name_does_not_leak_into_the_version(self):
        a = _make_archive(self.tmp / f"{NAME}-1.0-3.igos.tar.gz", NAME, "1.0", 3,
                          {"usr/bin/widget": b"x"})
        ok, msg = self.installer.install(NAME, archive_path=str(a),
                                         expected_sha256=_sha(a))
        self.assertTrue(ok, msg)
        row = self.db.get_installed(NAME)
        self.assertEqual((row["version"], int(row["release"])), ("1.0", 3))

    def test_an_archive_without_metadata_still_takes_the_name(self):
        bare = self.tmp / f"{NAME}-1.0.igos.tar.gz"
        with tarfile.open(bare, "w:gz") as tf:
            info = tarfile.TarInfo("usr/bin/widget")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
        ok, msg = self.installer.install(NAME, archive_path=str(bare),
                                         expected_sha256=_sha(bare))
        self.assertTrue(ok, msg)
        row = self.db.get_installed(NAME)
        self.assertEqual((row["version"], int(row["release"])), ("1.0", 1))


if __name__ == "__main__":
    unittest.main()
