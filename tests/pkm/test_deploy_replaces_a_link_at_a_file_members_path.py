# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A regular-file member is written AT its own path, never through a link.

tarfile writes a regular-file member with open(path, "wb"), which follows a
symbolic link already standing at that path. The deploy unlinked an existing
regular file there first (the ETXTBSY guard) but left a symlink in place, so
the member's bytes went THROUGH the link into whatever it named. Measured on a
build root on 2026-10-13: one package's archive carried a regular file at
usr/bin/typer, another installed package owned a symlink at that same path
pointing into its own tree (usr/bin/typer -> ../lib/erlang/bin/typer), and the
deploy rewrote the second package's file with the first package's 181-byte
script. `pkm verify` of the second package then reported that file modified,
and nothing at install time had said so.

The deploy now unlinks a symlink at a regular-file member's path exactly as it
unlinks a regular file: the link itself goes, the file it names is never
opened, and the member lands as a fresh regular file at its own path.
"""
import errno
import io
import os
import stat
import tarfile

from pkm.database import PackageDB
from pkm.installer import PackageInstaller, _safe_extract_tar

OTHER_BYTES = b"#!/bin/sh\necho the other package's tool\n"
MEMBER_BYTES = b"#!/usr/bin/python3\nimport sys\nfrom typer.cli import main\n"


def _payload_archive(path, name, members):
    """A .tar.gz of regular-file members (name -> bytes), optionally with a
    .PKGINFO so install() can read it."""
    with tarfile.open(path, "w:gz") as tf:
        if name is not None:
            info = (f"pkgname={name}\npkgver=1.0\npkgrel=1\npkgdesc=test\n"
                    f"license=GPL\ntier=extra\nbuilddate=2026-10-13T00:00:00Z\n"
                    f"size=64\nfilecount={len(members)}\n").encode()
            ti = tarfile.TarInfo("./.PKGINFO")
            ti.size = len(info)
            tf.addfile(ti, io.BytesIO(info))
        for member_name, data in members.items():
            ti = tarfile.TarInfo(member_name)
            ti.mode = 0o755
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return str(path)


def _root_with_link(tmp_path):
    """A root where usr/bin/x is a symlink to ../lib/a/x, a regular file
    holding another package's bytes."""
    root = tmp_path / "root"
    (root / "usr" / "lib" / "a").mkdir(parents=True)
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "lib" / "a" / "x").write_bytes(OTHER_BYTES)
    os.symlink("../lib/a/x", root / "usr" / "bin" / "x")
    return root


def _assert_member_landed_at_its_own_path(test_root):
    landed = os.lstat(test_root / "usr" / "bin" / "x")
    assert stat.S_ISREG(landed.st_mode), (
        "the member must replace the link with a regular file, "
        f"found mode {oct(landed.st_mode)}")
    assert (test_root / "usr" / "bin" / "x").read_bytes() == MEMBER_BYTES
    assert (test_root / "usr" / "lib" / "a" / "x").read_bytes() == OTHER_BYTES, (
        "the file the link named must keep its own bytes")


def test_the_deploy_replaces_a_link_instead_of_writing_through_it(tmp_path):
    root = _root_with_link(tmp_path)
    archive = _payload_archive(tmp_path / "pkg.tar.gz", None,
                               {"./usr/bin/x": MEMBER_BYTES})

    ok, msg = _safe_extract_tar(archive, root)

    assert ok, msg
    _assert_member_landed_at_its_own_path(root)


def test_a_dangling_link_does_not_create_the_file_it_names(tmp_path):
    """A link whose target does not exist: writing through it would CREATE the
    target, a file at a path the archive never named."""
    root = tmp_path / "root"
    (root / "usr" / "bin").mkdir(parents=True)
    os.symlink("../lib/b/y", root / "usr" / "bin" / "y")
    (root / "usr" / "lib" / "b").mkdir(parents=True)
    archive = _payload_archive(tmp_path / "pkg.tar.gz", None,
                               {"./usr/bin/y": MEMBER_BYTES})

    ok, msg = _safe_extract_tar(archive, root)

    assert ok, msg
    landed = os.lstat(root / "usr" / "bin" / "y")
    assert stat.S_ISREG(landed.st_mode)
    assert (root / "usr" / "bin" / "y").read_bytes() == MEMBER_BYTES
    assert not (root / "usr" / "lib" / "b" / "y").exists(), (
        "nothing may be written at the path the dangling link named")


def test_a_link_that_cannot_be_removed_fails_the_extract(tmp_path, monkeypatch):
    """A link the deploy cannot remove is a link the member would be written
    through: the extract stops and says why, and the linked file keeps its
    bytes."""
    root = _root_with_link(tmp_path)
    archive = _payload_archive(tmp_path / "pkg.tar.gz", None,
                               {"./usr/bin/x": MEMBER_BYTES})
    real_unlink = os.unlink

    def refuse_the_link(path, *args, **kwargs):
        if str(path).endswith(os.path.join("usr", "bin", "x")):
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", refuse_the_link)
    ok, msg = _safe_extract_tar(archive, root)

    assert not ok
    assert "Permission denied" in msg, msg
    assert os.path.islink(root / "usr" / "bin" / "x")
    assert (root / "usr" / "lib" / "a" / "x").read_bytes() == OTHER_BYTES


def test_an_install_replaces_an_unowned_link_at_its_path(tmp_path):
    """The same through PackageInstaller.install: the package's file lands at
    its own path, is recorded there, and the linked file keeps its bytes."""
    root = _root_with_link(tmp_path)
    db = PackageDB(tmp_path / "pkm.db", root=str(root))
    try:
        archive = _payload_archive(tmp_path / "beta-1.0.igos.tar.gz", "beta",
                                   {"./usr/bin/x": MEMBER_BYTES})
        ok, msg = PackageInstaller(db, root=str(root)).install(
            "beta", archive_path=archive, install_reason="manual")
        assert ok, msg
        _assert_member_landed_at_its_own_path(root)
        result = db.verify_package("beta")
        assert not result["missing"] and not result["modified"], result
    finally:
        db.close()
