# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An install never changes the kind of a file another installed package owns.

Measured on a build root on 2026-10-13: an archive carried a regular file at
usr/bin/typer while another installed package owned a symbolic link at that
same path. Nothing checked the archive's paths against the files other
packages own, so the install went ahead, and the other package's file was
rewritten through its link (the write-through itself is closed separately, in
tests/pkm/test_deploy_replaces_a_link_at_a_file_members_path.py).

Two packages owning the same path is not by itself a conflict on this system:
the second-pass and bootstrap builds of a package ship the same paths as the
package itself, and an installed machine carries over a hundred thousand such
co-owned paths, each the same kind of file in both packages. What two
packages cannot both have is a different KIND of file at one path: a regular
file where the other package keeps a link, or a link where it keeps a regular
file. One of them would lose its file, and the loser's verify would report it.

So the install refuses such an archive before anything is written, naming the
path and the package that owns it. A path shared as the same kind of file
stays shared, and the install names each co-owning package with its count, so
the sharing is never silent. A package the archive declares it supersedes is
not a conflict: its ownership moves to the new package in the same
transaction. Directories and configuration files are outside this check.
"""
import io
import os
import stat
import tarfile

from pkm.database import PackageDB
from pkm.installer import PackageInstaller
from pkm.output import Reporter

ALPHA_TOOL = b"#!/bin/sh\necho alpha\n"
BETA_SCRIPT = b"#!/usr/bin/python3\nprint('beta')\n"


def _archive(tmp_path, name, files=None, links=None, supersedes=None):
    """A minimal .igos.tar.gz: regular files (path -> bytes), symbolic links
    (path -> target), and optionally a staged manifest declaring SUPERSEDES."""
    files = dict(files or {})
    links = dict(links or {})
    if supersedes:
        manifest = (f"SUPERSEDES: {supersedes}\nFILE LIST:\n"
                    + "".join(f"{p}\n" for p in list(files) + list(links)))
        files[f"var/lib/igos/packages/{name}-1.0"] = manifest.encode()
    path = tmp_path / f"{name}-1.0.igos.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        info = (f"pkgname={name}\npkgver=1.0\npkgrel=1\npkgdesc=test\n"
                f"license=GPL\ntier=extra\nbuilddate=2026-10-13T00:00:00Z\n"
                f"size=64\nfilecount={len(files) + len(links)}\n").encode()
        ti = tarfile.TarInfo("./.PKGINFO")
        ti.size = len(info)
        tf.addfile(ti, io.BytesIO(info))
        for rel, data in files.items():
            ti = tarfile.TarInfo(f"./{rel}")
            ti.mode = 0o755
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        for rel, target in links.items():
            ti = tarfile.TarInfo(f"./{rel}")
            ti.type = tarfile.SYMTYPE
            ti.linkname = target
            ti.mode = 0o777
            tf.addfile(ti)
    return str(path)


def _setup(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    db = PackageDB(tmp_path / "pkm.db", root=str(root))
    return root, db, PackageInstaller(db, root=str(root))


def _install(installer, tmp_path, name, reporter=None, **members):
    return installer.install(name, archive_path=_archive(tmp_path, name, **members),
                             install_reason="manual", reporter=reporter)


def _install_alpha_with_a_link(installer, tmp_path):
    ok, msg = _install(installer, tmp_path, "alpha",
                       files={"usr/lib/alpha/x": ALPHA_TOOL},
                       links={"usr/bin/x": "../lib/alpha/x"})
    assert ok, msg


def test_a_file_over_another_packages_link_is_refused_naming_the_owner(tmp_path):
    root, db, installer = _setup(tmp_path)
    try:
        _install_alpha_with_a_link(installer, tmp_path)

        ok, msg = _install(installer, tmp_path, "beta",
                           files={"usr/bin/x": BETA_SCRIPT,
                                  "usr/share/beta/readme": b"beta\n"})

        assert not ok
        assert "/usr/bin/x" in msg and "alpha" in msg, msg
        # Nothing was written: alpha's link and the file it names are as they
        # were, beta's other file never landed, and beta is not recorded.
        assert os.readlink(root / "usr" / "bin" / "x") == "../lib/alpha/x"
        assert (root / "usr" / "lib" / "alpha" / "x").read_bytes() == ALPHA_TOOL
        assert not (root / "usr" / "share" / "beta").exists()
        assert db.get_installed("beta") is None
        assert not (root / "var" / "lib" / "igos" / "packages" / "beta-1.0").exists()
    finally:
        db.close()


def test_a_link_over_another_packages_file_is_refused_naming_the_owner(tmp_path):
    root, db, installer = _setup(tmp_path)
    try:
        ok, msg = _install(installer, tmp_path, "alpha",
                           files={"usr/bin/y": ALPHA_TOOL})
        assert ok, msg

        ok, msg = _install(installer, tmp_path, "beta",
                           files={"usr/lib/beta/y": BETA_SCRIPT},
                           links={"usr/bin/y": "../lib/beta/y"})

        assert not ok
        assert "/usr/bin/y" in msg and "alpha" in msg, msg
        landed = os.lstat(root / "usr" / "bin" / "y")
        assert stat.S_ISREG(landed.st_mode)
        assert (root / "usr" / "bin" / "y").read_bytes() == ALPHA_TOOL
        assert not (root / "usr" / "lib" / "beta").exists()
        assert db.get_installed("beta") is None
    finally:
        db.close()


def test_the_same_kind_at_a_shared_path_is_co_ownership_and_is_named(tmp_path):
    root, db, installer = _setup(tmp_path)
    try:
        ok, msg = _install(installer, tmp_path, "alpha",
                           files={"usr/share/common/f": b"one\n",
                                  "usr/share/common/g": b"two\n"})
        assert ok, msg
        out = io.StringIO()
        reporter = Reporter(stream=out, err_stream=io.StringIO())

        ok, msg = _install(installer, tmp_path, "beta", reporter=reporter,
                           files={"usr/share/common/f": b"one\n",
                                  "usr/share/common/g": b"two\n",
                                  "usr/bin/beta": BETA_SCRIPT})

        assert ok, msg
        assert "co-owned with alpha: 2 paths" in msg, msg
        assert "co-owned with alpha: 2 paths" in out.getvalue(), out.getvalue()
        owners = {r[0] for r in db.conn.execute(
            "SELECT i.name FROM files f JOIN installed i ON f.package_id = i.id "
            "WHERE f.path = 'usr/share/common/f'")}
        assert owners == {"alpha", "beta"}
    finally:
        db.close()


def test_a_declared_predecessor_is_not_a_conflict(tmp_path):
    root, db, installer = _setup(tmp_path)
    try:
        _install_alpha_with_a_link(installer, tmp_path)

        ok, msg = _install(installer, tmp_path, "beta",
                           files={"usr/bin/x": BETA_SCRIPT},
                           supersedes="alpha")

        assert ok, msg
        assert db.get_installed("alpha")["superseded_by"] == "beta"
        landed = os.lstat(root / "usr" / "bin" / "x")
        assert stat.S_ISREG(landed.st_mode)
        assert (root / "usr" / "bin" / "x").read_bytes() == BETA_SCRIPT
        assert (root / "usr" / "lib" / "alpha" / "x").read_bytes() == ALPHA_TOOL
    finally:
        db.close()
