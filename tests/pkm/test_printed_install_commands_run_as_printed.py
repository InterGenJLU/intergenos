# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The install commands pkm prints to offer a deliberate install of a local
archive run as printed.

WHY (measured 2026-09-22 by a second reader). Three messages told a person to
run ``pkm install --archive PATH --archive-trust loose``. Run as printed, that
exits 2: ``pkm install`` requires the package name. One of the three -- the
refusal of an archive whose filename and sealed header name different builds --
was also returned to every caller that passes no mode (the repository download
inside ``pkm install``, reinstall, upgrade, the proprietary-helper install, the
graphical installer), where switching to loose trust skips the repository
verification those paths rely on.

WHAT IS PINNED. Each message is produced by the shipped code, the command is
taken out of the text a person sees, and pkm's own parser reads it: it names
the package, the archive and loose trust, and the install root when that is
not "/". The command stays on one line when the message is wrapped for the
terminal, because a command split over two lines, copied from the terminal,
runs as two broken commands. The name-and-header refusal offers the command
only when the person named the archive on the command line.
"""
import argparse
import contextlib
import hashlib
import io
import re
import shlex
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from pkm import cli, rootpaths
from pkm.database import PackageDB
from pkm.installer import PackageInstaller
from pkm.output import _wrap_prose

# A pkm command quoted in a message, and only when it is on one line.
QUOTED_PKM_COMMAND = re.compile(r"`(pkm [^`\n]*)`")


def _archive(tmp_path: Path, directory: Path, filename: str, name: str,
             version: str, release: int) -> Path:
    staging = tmp_path / "staging" / filename
    (staging / "usr").mkdir(parents=True, exist_ok=True)
    (staging / "usr" / f"{name}.txt").write_text("payload\n")
    (staging / ".PKGINFO").write_text(
        f"pkgname={name}\npkgver={version}\npkgrel={release}\npkgdesc=test\n"
        f"license=GPL\ntier=core\nbuilddate=2026-09-22T00:00:00Z\nsize=8\n"
        f"filecount=1\n")
    path = directory / filename
    with tarfile.open(path, "w:gz") as tf:
        tf.add(staging / "usr" / f"{name}.txt", arcname=f"usr/{name}.txt")
        tf.add(staging / ".PKGINFO", arcname=".PKGINFO")
    return path


def _flat(text: str) -> str:
    """The words of a wrapped message, for reading a sentence out of it."""
    return " ".join(text.split())


def _the_install_command(text: str) -> str:
    found = [c for c in QUOTED_PKM_COMMAND.findall(text) if " --archive " in c]
    assert len(found) == 1, (
        f"expected one install command, on one line, in:\n{text}\nfound: {found!r}")
    return found[0]


def _parse(command: str) -> argparse.Namespace:
    words = shlex.split(command)
    assert words[0] == "pkm", command
    try:
        return cli.build_parser().parse_args(words[1:])
    except SystemExit as exc:
        pytest.fail(f"the printed command does not parse (exit {exc.code}): "
                    f"{command}")


def _what_it_installs(args: argparse.Namespace):
    return (args.command, args.packages, args.archive, args.archive_trust,
            args.root)


def _install_args(archive=None, trust="strict"):
    return argparse.Namespace(
        packages=["demo"], archive=(str(archive) if archive else None),
        archive_trust=trust, quiet=False, verbose=False,
        allow_downgrade=False, assume_yes=True)


def _cmd_install(tmp_path, root: Path, index_entry, args):
    """Run the shipped `pkm install` code in-process against a scratch root.

    The package installer is the real one; the repository is stood in for, and
    so are the pre-transaction hook and the next-steps block. Returns what the
    person would see on the error stream, and the exit status."""
    db = PackageDB(tmp_path / "pkm.db")
    err = io.StringIO()
    status = None
    cli.set_install_root(str(root))
    try:
        with patch("pkm.cli.RepoManager") as Repo, \
             patch("pkm.pretxn.run_pre_transaction_hook"), \
             patch("pkm.cli._print_transaction_next_steps"):
            Repo.return_value.get_package.return_value = index_entry
            Repo.return_value.resolve_dependencies.return_value = (
                False, "the repository is not reached in this test")
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(err):
                try:
                    cli.cmd_install(db, args)
                except SystemExit as exc:
                    status = exc.code
    finally:
        cli.set_install_root(None)
        db.close()
    return err.getvalue(), status


def _index_entry_for(archive: Path):
    return {"name": "demo", "version": "1.0", "release": 11,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}


# -- 1. the refusal of an unverified local archive (pkm/installer.py) -------

def test_the_unverified_local_archive_refusal_prints_a_command_that_runs(tmp_path):
    root = tmp_path / "a root"          # a space: the command must quote it
    archive_dir = rootpaths.archive_dir(root)
    archive_dir.mkdir(parents=True)
    archive = _archive(tmp_path, archive_dir, "demo-1.0-7.igos.tar.gz",
                       "demo", "1.0", 7)
    db = PackageDB(tmp_path / "pkm.db")
    try:
        ok, msg = PackageInstaller(db, root=str(root)).install("demo")
    finally:
        db.close()
    assert not ok and "no signed-index verification reference" in msg, msg
    args = _parse(_the_install_command(msg))
    assert _what_it_installs(args) == (
        "install", ["demo"], str(archive), "loose", str(root))


def test_the_same_refusal_on_this_system_names_no_root(tmp_path):
    archive = _archive(tmp_path, tmp_path, "demo-1.0-7.igos.tar.gz",
                       "demo", "1.0", 7)
    db = PackageDB(tmp_path / "pkm.db")
    try:
        installer = PackageInstaller(db)
        # The archive directory of "/" is this machine's own; the resolver is
        # pointed at the scratch file instead of reading it.
        installer._find_archive = lambda name: archive
        ok, msg = installer.install("demo")
    finally:
        db.close()
    assert not ok and "no signed-index verification reference" in msg, msg
    args = _parse(_the_install_command(msg))
    assert _what_it_installs(args) == (
        "install", ["demo"], str(archive), "loose", None)


# -- 2. the refusal of a name its header contradicts (pkm/installer.py) -----

def test_a_person_who_named_the_archive_is_offered_a_command_that_runs(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    archive = _archive(tmp_path, tmp_path, "demo-1.0-11.igos.tar.gz",
                       "demo", "1.0", 7)
    err, status = _cmd_install(tmp_path, root, _index_entry_for(archive),
                               _install_args(archive, "strict"))
    assert status == 1, err
    assert "Refusing to install demo-1.0-11.igos.tar.gz" in _flat(err), err
    args = _parse(_the_install_command(err))
    assert _what_it_installs(args) == (
        "install", ["demo"], str(archive), "loose", str(root))


def test_a_cached_archive_is_refused_without_offering_loose_trust(tmp_path):
    """`pkm install demo` with no --archive: the cached archive matches the
    signed index, so it is installed from the cache -- and its filename
    disagrees with its header. The person never named a file; loose trust is
    not the remedy here."""
    root = tmp_path / "root"
    archive_dir = rootpaths.archive_dir(root)
    archive_dir.mkdir(parents=True)
    archive = _archive(tmp_path, archive_dir, "demo-1.0-11.igos.tar.gz",
                       "demo", "1.0", 7)
    err, status = _cmd_install(tmp_path, root, _index_entry_for(archive),
                               _install_args())
    assert status == 1, err
    assert "Refusing to install demo-1.0-11.igos.tar.gz" in _flat(err), err
    assert "Rename the file to what its header states." in _flat(err), err
    assert "--archive-trust loose" not in _flat(err), err
    assert QUOTED_PKM_COMMAND.findall(err) == [], err


def test_every_other_caller_is_refused_without_offering_loose_trust(tmp_path):
    """install() as the graphical installer, upgrade and reinstall call it:
    with an archive path and no mode."""
    root = tmp_path / "root"
    root.mkdir()
    archive = _archive(tmp_path, tmp_path, "demo-1.0-11.igos.tar.gz",
                       "demo", "1.0", 7)
    db = PackageDB(tmp_path / "pkm.db")
    try:
        ok, msg = PackageInstaller(db, root=str(root)).install(
            "demo", archive_path=str(archive))
    finally:
        db.close()
    assert not ok, msg
    assert msg.endswith("Rename the file to what its header states."), msg
    assert "--archive-trust loose" not in msg, msg


# -- 3. the cached archive the signed index does not know (pkm/cli.py) ------

def test_the_cached_archive_warning_prints_a_command_that_runs(tmp_path):
    root = tmp_path / "root"
    archive_dir = rootpaths.archive_dir(root)
    archive_dir.mkdir(parents=True)
    archive = _archive(tmp_path, archive_dir, "demo-1.0-7.igos.tar.gz",
                       "demo", "1.0", 7)
    err, status = _cmd_install(tmp_path, root, None, _install_args())
    assert "is not in the signed index" in _flat(err), err
    args = _parse(_the_install_command(err))
    assert _what_it_installs(args) == (
        "install", ["demo"], str(archive), "loose", str(root))
    assert status == 1, err       # the repository stand-in has no such package


# -- the terminal: a quoted command is one line -----------------------------

def test_a_quoted_command_is_never_split_across_lines():
    command = ("pkm --root /mnt/target install demo --archive "
               "/mnt/target/var/lib/igos/archives/demo-1.0-11.igos.tar.gz "
               "--archive-trust loose")
    text = ("Refusing to install demo-1.0-11.igos.tar.gz: its filename says "
            "demo 1.0 release 11, but its sealed header says demo 1.0 release "
            f"7. Nothing was changed. Rename the file to what its header "
            f"states, or install it deliberately with `{command}`.")
    wrapped = _wrap_prose(text)
    assert len(wrapped.splitlines()) > 1, "the prose around it still wraps"
    assert QUOTED_PKM_COMMAND.findall(wrapped) == [command], wrapped
