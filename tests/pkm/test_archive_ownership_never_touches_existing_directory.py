# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An archive's recorded ownership must never reach a directory, and must
never hand a system path to an ordinary user.

Measured on a running installation 2026-09-20: a user-built tarball carrying
the directory entries ``usr/`` and ``usr/bin/`` recorded as uid 1000 was
installed, and afterwards the machine's real ``/usr`` and ``/usr/bin`` were
owned by that ordinary user.  An unprivileged account could then create files
in ``/usr/bin``, and sshd refused its authorized-keys command with "bad
ownership or modes for directory /usr/bin" on every authentication.

Two rules follow, and these tests hold the deploy path to both.

1. A directory member's recorded ownership never reaches a directory the
   TARGET ALREADY HAD.  That directory belongs to the machine, whoever the
   archive says owns it, and it is the measured case above.  (This rule was
   once "never applied to any directory", which drew the line by member type
   rather than by whose the object is; it was completed in the release that
   added tests/pkm/test_archive_directory_ownership_completes_the_file_rule.py
   — a directory THIS deploy created is the package's own and takes its
   recorded system-account ownership, exactly as a file member does.  The
   tests below are unaffected either way: the directories they name either
   already exist or record an ordinary account.)  No shipped recipe stages
   non-root ownership into its archive: every ``chown`` to a service account
   in ``packages/`` runs in ``post_install``, and the builder's staging
   chokepoint (``igos-build/builder.py``, ``_force_root_ownership``) forces
   root:root on anything staged with an id at or above 1000.

2. A file member whose recorded owner resolves to an ORDINARY account on the
   target (uid or gid at or above 1000, the same line the builder's chokepoint
   draws) is left root-owned and reported.  The PI-Z11 restore that this rule
   narrows stays intact for the system accounts it was written for -- ``wall``
   as ``root:tty``, and the at/fcron/dbus binaries -- whose ids are below 1000.
   A member that also carries setuid or setgid and names an ordinary owner
   fails the install outright: a setuid binary owned by an ordinary user is a
   privilege-escalation primitive, and masking it would be worse than refusing.

The tests run unprivileged, so a real chown to a foreign id is impossible.
They record chown intent through a monkeypatch, exactly as the PI-Z11 tests in
tests/pkm/test_eula_and_ownership_wave.py do, and assert on what the deploy
path TRIED to do.
"""
import io
import os
import stat
import tarfile
from pathlib import Path

import pytest

from pkm.database import PackageDB
from pkm.installer import PackageInstaller


def _archive(tmp, name, members):
    """A minimal .igos.tar.gz whose members are given as dicts.

    Each member dict: name, plus either ``data`` (a regular file) or
    ``isdir=True``, plus mode/uname/gname.
    """
    lines = [f"pkgname={name}", "pkgver=1.0", "pkgrel=1",
             "pkgdesc=test pkg", "license=GPL", "tier=core",
             "builddate=2026-09-20T00:00:00Z", "size=64", "filecount=1"]
    archive = Path(tmp) / f"{name}-1.0.igos.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        blob = ("\n".join(lines) + "\n").encode()
        ti = tarfile.TarInfo("./.PKGINFO")
        ti.size = len(blob)
        tf.addfile(ti, io.BytesIO(blob))
        for m in members:
            ti = tarfile.TarInfo(m["name"])
            ti.mode = m["mode"]
            ti.uname = m.get("uname", "root")
            ti.gname = m.get("gname", "root")
            if m.get("isdir"):
                ti.type = tarfile.DIRTYPE
                ti.size = 0
                tf.addfile(ti)
            else:
                ti.size = len(m["data"])
                tf.addfile(ti, io.BytesIO(m["data"]))
    return str(archive)


def _target_root(tmp_path):
    """A throwaway install root whose OWN passwd/group DEFINE the names.

    This is the piece that decides whether the defect reproduces: the deploy
    path resolves an archive's uname against the TARGET's databases, so a
    throwaway root that does not define the name cannot show the chown at all.
    """
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "passwd").write_text(
        "root:x:0:0:root:/root:/bin/bash\n"
        "messagebus:x:18:18:D-Bus:/run/dbus:/usr/bin/false\n"
        "ordinary:x:1000:1000:An ordinary account:/home/ordinary:/bin/bash\n"
    )
    (root / "etc" / "group").write_text(
        "root:x:0:\ntty:x:5:\nmessagebus:x:18:\nordinary:x:1000:\n"
    )
    return root


def _recording_chown(monkeypatch):
    """Record every chown the deploy path attempts, and perform none."""
    events = []

    def rec(path, uid, gid, **kw):
        events.append((str(path), uid, gid))

    monkeypatch.setattr(os, "chown", rec)
    return events


def _install(tmp_path, root, name, members):
    db = PackageDB(tmp_path / f"{name}.db")
    try:
        inst = PackageInstaller(db, root=str(root))
        return inst.install(name, archive_path=_archive(tmp_path, name, members),
                            install_reason="manual")
    finally:
        db.close()


# ── Rule 1: a directory's recorded ownership is never applied ─────────────

def test_an_existing_directory_is_not_chowned_from_archive_metadata(
        tmp_path, monkeypatch, capsys):
    """The measured shape: usr/ and usr/bin/ recorded as an ordinary user.

    Both directories already exist and are root-owned before the install, as
    they are on any running system.  Nothing in the deploy path may chown
    them.
    """
    root = _target_root(tmp_path)
    (root / "usr" / "bin").mkdir(parents=True)
    events = _recording_chown(monkeypatch)

    ok, msg = _install(tmp_path, root, "dirpkg", [
        {"name": "usr/", "isdir": True, "mode": 0o755,
         "uname": "ordinary", "gname": "ordinary"},
        {"name": "usr/bin/", "isdir": True, "mode": 0o755,
         "uname": "ordinary", "gname": "ordinary"},
        {"name": "usr/bin/dirpkg-payload", "data": b"x\n", "mode": 0o644},
    ])
    assert ok, f"install failed: {msg}"

    touched = [e for e in events
               if e[0].rstrip("/").endswith(("/usr", "/usr/bin"))]
    assert not touched, (
        "an archive's directory ownership reached a directory that already "
        f"existed: {touched}"
    )
    err = capsys.readouterr().err
    assert "ordinary" in err, (
        "a directory member recording a non-root owner must be reported"
    )


def test_a_directory_the_install_creates_is_left_root_owned(
        tmp_path, monkeypatch, capsys):
    """Same rule when the directory did not exist: created root, left root."""
    root = _target_root(tmp_path)
    events = _recording_chown(monkeypatch)

    ok, msg = _install(tmp_path, root, "newdirpkg", [
        {"name": "opt/newdirpkg/", "isdir": True, "mode": 0o755,
         "uname": "ordinary", "gname": "ordinary"},
        {"name": "opt/newdirpkg/payload", "data": b"x\n", "mode": 0o644},
    ])
    assert ok, f"install failed: {msg}"
    assert (root / "opt" / "newdirpkg").is_dir(), "directory was not created"

    touched = [e for e in events if e[0].rstrip("/").endswith("/opt/newdirpkg")]
    assert not touched, (
        f"a newly created directory was chowned from archive metadata: {touched}"
    )
    assert "ordinary" in capsys.readouterr().err, (
        "a directory member recording a non-root owner must be reported"
    )


# ── Rule 2: an ordinary account never receives a file in a system path ────

def test_a_file_member_owned_by_an_ordinary_account_is_left_root_owned(
        tmp_path, monkeypatch, capsys):
    """uid 1000 in the archive is the build-user leak class, refused here."""
    root = _target_root(tmp_path)
    (root / "usr" / "bin").mkdir(parents=True)
    events = _recording_chown(monkeypatch)

    ok, msg = _install(tmp_path, root, "leakpkg", [
        {"name": "usr/bin/leakpkg-tool", "data": b"#!/bin/sh\n", "mode": 0o755,
         "uname": "ordinary", "gname": "ordinary"},
    ])
    assert ok, f"install failed: {msg}"

    touched = [e for e in events if e[0].endswith("leakpkg-tool")]
    assert not touched, (
        f"a system-path file was handed to an ordinary account: {touched}"
    )
    err = capsys.readouterr().err
    assert "leakpkg-tool" in err and "ordinary" in err, (
        "an ordinary-account file member must be reported, not silently kept"
    )


def test_a_setuid_file_member_owned_by_an_ordinary_account_refuses_the_install(
        tmp_path, monkeypatch):
    """A setuid binary owned by an ordinary user is refused, never masked."""
    root = _target_root(tmp_path)
    (root / "usr" / "bin").mkdir(parents=True)
    _recording_chown(monkeypatch)

    ok, msg = _install(tmp_path, root, "suidleakpkg", [
        {"name": "usr/bin/suidleakpkg-tool", "data": b"#!/bin/sh\n",
         "mode": 0o4755, "uname": "ordinary", "gname": "ordinary"},
    ])
    assert not ok, "a setuid member owned by an ordinary account was installed"
    assert "suidleakpkg-tool" in msg and "ordinary" in msg, (
        f"the refusal must name the member and the owner; got: {msg}"
    )


# ── The PI-Z11 restore this narrows must still work ──────────────────────

def test_a_system_account_file_member_is_still_restored(tmp_path, monkeypatch):
    """`wall 2755 root:tty` and its class keep their recorded ownership."""
    root = _target_root(tmp_path)
    (root / "usr" / "bin").mkdir(parents=True)
    events = _recording_chown(monkeypatch)

    ok, msg = _install(tmp_path, root, "wallpkg2", [
        {"name": "usr/bin/wall", "data": b"#!/bin/sh\n", "mode": 0o2755,
         "uname": "root", "gname": "tty"},
    ])
    assert ok, f"install failed: {msg}"

    touched = [e for e in events if e[0].endswith("/usr/bin/wall")]
    assert touched, "the PI-Z11 ownership restore stopped working for root:tty"
    assert touched[0][1] == 0 and touched[0][2] == 5, (
        f"expected chown(0, 5) resolved from the target group db, got {touched[0]}"
    )


def test_a_system_user_file_member_below_the_ordinary_line_is_restored(
        tmp_path, monkeypatch):
    """The dbus helper class: uid 18 is a system id and is still applied."""
    root = _target_root(tmp_path)
    (root / "usr" / "libexec").mkdir(parents=True)
    events = _recording_chown(monkeypatch)

    ok, msg = _install(tmp_path, root, "buspkg", [
        {"name": "usr/libexec/bus-launch-helper", "data": b"#!/bin/sh\n",
         "mode": 0o4750, "uname": "root", "gname": "messagebus"},
    ])
    assert ok, f"install failed: {msg}"

    touched = [e for e in events if e[0].endswith("bus-launch-helper")]
    assert touched, "a system-group member lost its ownership restore"
    assert touched[0][1] == 0 and touched[0][2] == 18, (
        f"expected chown(0, 18) from the target group db, got {touched[0]}"
    )


# ── The report has to name a path a person can act on ────────────────────

def test_the_report_names_the_path_as_it_exists_on_the_target(
        tmp_path, monkeypatch, capsys):
    """Real archives record members with a leading `./`.

    The messages used to render that straight through, so every warning named
    `/./usr/bin` — a path that no command accepts. Measured under --root on
    2026-09-20 before this was corrected.
    """
    root = _target_root(tmp_path)
    (root / "usr" / "bin").mkdir(parents=True)
    _recording_chown(monkeypatch)

    ok, msg = _install(tmp_path, root, "dotpkg", [
        {"name": "./usr/", "isdir": True, "mode": 0o755,
         "uname": "ordinary", "gname": "ordinary"},
        {"name": "./usr/bin/", "isdir": True, "mode": 0o755,
         "uname": "ordinary", "gname": "ordinary"},
        {"name": "./usr/bin/dotpkg-tool", "data": b"x\n", "mode": 0o644,
         "uname": "ordinary", "gname": "ordinary"},
    ])
    assert ok, f"install failed: {msg}"

    err = capsys.readouterr().err
    assert "/./" not in err, (
        f"a report named a path that does not exist on the target:\n{err}"
    )
    for expected in ("/usr", "/usr/bin", "/usr/bin/dotpkg-tool"):
        assert expected in err, f"the report does not name {expected}:\n{err}"
