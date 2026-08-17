# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Regression tests for the PI-Z6 + PI-Z11 install-path wave (2026-07-06).

PI-Z6  — the EULA gate's helper ships INSIDE the package it gates, so on a
         first install no filesystem copy exists and the gate was
         unrunnable-by-construction (caught on the first NVIDIA-hardware
         Forge install). The gate now falls back to running the helper
         bundled in the signature-verified archive being installed.

PI-Z11 — deploy restored setuid/setgid BITS but not OWNERSHIP: a member the
         archive records as `wall 2755 root:tty` landed setgid-ROOT on the
         installed system. Deploy now restores non-root uname/gname resolved
         against the INSTALL TARGET's passwd/group, chown strictly BEFORE
         the mode re-apply (chown clears s-bits).

Tests run unprivileged, so real chown to foreign ids is impossible — the
ownership tests record os.chown intent via monkeypatch and assert the
call-before-chmod ordering; the mode restore itself is asserted on disk.
"""
import os
import stat
import tarfile
from pathlib import Path

import pytest

import pkm.installer as installer_mod
from pkm.database import PackageDB
from pkm.installer import PackageInstaller


def _build_archive(tmp, name, version, extra_pkginfo=(), members=()):
    """Minimal .igos.tar.gz: .PKGINFO + payload members.

    members: iterable of (arcname, bytes, mode, uname, gname).
    """
    lines = [f"pkgname={name}", f"pkgver={version}", "pkgrel=1",
             "pkgdesc=test pkg", "license=GPL", "tier=core",
             "builddate=2026-07-06T00:00:00Z", "size=64", "filecount=1"]
    lines += list(extra_pkginfo)
    archive = Path(tmp) / f"{name}-{version}.igos.tar.gz"
    import io
    with tarfile.open(archive, "w:gz") as tf:
        data = ("\n".join(lines) + "\n").encode()
        ti = tarfile.TarInfo("./.PKGINFO")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
        for arcname, payload, mode, uname, gname in members:
            ti = tarfile.TarInfo(arcname)
            ti.size = len(payload)
            ti.mode = mode
            ti.uname = uname
            ti.gname = gname
            tf.addfile(ti, io.BytesIO(payload))
    return str(archive)


def _target_root(tmp_path, groups_extra=""):
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
    (root / "etc" / "group").write_text("root:x:0:\ntty:x:5:\n" + groups_extra)
    return root


# ── PI-Z11: ownership restore ─────────────────────────────────────────────

def test_deploy_restores_nonroot_group_before_mode(tmp_path, monkeypatch):
    """`wall 2755 root:tty` must chown(0,5) BEFORE the sgid re-apply."""
    root = _target_root(tmp_path)
    events = []
    real_chown = os.chown

    def rec_chown(path, uid, gid, **kw):
        events.append(("chown", str(path), uid, gid))
        # do not actually chown (unprivileged); ownership intent is the assert

    monkeypatch.setattr(os, "chown", rec_chown)
    real_chmod = Path.chmod

    def rec_chmod(self, mode, **kw):
        if "wall" in str(self):
            events.append(("chmod", str(self), mode))
        return real_chmod(self, mode, **kw)

    monkeypatch.setattr(Path, "chmod", rec_chmod)

    db = PackageDB(tmp_path / "t.db")
    try:
        inst = PackageInstaller(db, root=str(root))
        archive = _build_archive(
            tmp_path, "wallpkg", "1.0",
            members=[("usr/bin/wall", b"#!/bin/sh\n", 0o2755, "root", "tty")])
        ok, msg = inst.install("wallpkg", archive_path=archive,
                               install_reason="manual")
        assert ok, f"install failed: {msg}"
    finally:
        db.close()

    chowns = [e for e in events if e[0] == "chown" and "wall" in e[1]]
    assert chowns, "PI-Z11: no ownership restore attempted for root:tty member"
    assert chowns[0][2] == 0 and chowns[0][3] == 5, \
        f"expected chown(0, 5) resolved from the TARGET group db, got {chowns[0]}"
    wall_chmods = [e for e in events if e[0] == "chmod"]
    assert wall_chmods, "sgid mode re-apply missing"
    # ordering: the restore chown must precede the final mode application
    idx_chown = events.index(chowns[0])
    idx_chmod = max(i for i, e in enumerate(events) if e[0] == "chmod")
    assert idx_chown < idx_chmod, \
        "PI-Z11 ordering: chown must precede chmod (chown clears s-bits)"
    deployed = root / "usr" / "bin" / "wall"
    assert deployed.stat().st_mode & stat.S_ISGID, "sgid bit not restored"


def test_deploy_warns_on_unresolvable_owner(tmp_path, capsys):
    """A name the target lacks is warned loudly, never silently dropped."""
    root = _target_root(tmp_path)
    db = PackageDB(tmp_path / "t.db")
    try:
        inst = PackageInstaller(db, root=str(root))
        archive = _build_archive(
            tmp_path, "ghostpkg", "1.0",
            members=[("usr/bin/ghost", b"x", 0o755, "root", "nosuchgroup")])
        ok, msg = inst.install("ghostpkg", archive_path=archive,
                               install_reason="manual")
        assert ok, f"install failed: {msg}"
    finally:
        db.close()
    err = capsys.readouterr().err
    assert "nosuchgroup" in err and "WARNING" in err, \
        "PI-Z11: unresolvable owner must warn loudly"


# ── PI-Z6: EULA helper first-install fallback ─────────────────────────────

def _eula_members(exit_code):
    script = f"#!/bin/sh\nexit {exit_code}\n".encode()
    return [("usr/lib/intergen/eula-helpers/test-eula", script, 0o755,
             "root", "root")]


def test_eula_helper_runs_from_archive_on_first_install(tmp_path, monkeypatch):
    """No filesystem helper + helper in the archive → gate runs it, install proceeds."""
    monkeypatch.setattr(installer_mod, "EULA_HELPER_DIR",
                        tmp_path / "no-helpers-here")
    root = _target_root(tmp_path)
    db = PackageDB(tmp_path / "t.db")
    try:
        inst = PackageInstaller(db, root=str(root))
        archive = _build_archive(tmp_path, "eulapkg", "1.0",
                                 extra_pkginfo=["eula_helper=test-eula"],
                                 members=_eula_members(0))
        ok, msg = inst.install("eulapkg", archive_path=archive,
                               install_reason="manual")
        assert ok, f"PI-Z6: first-install gate should pass via archive helper: {msg}"
        assert db.get_installed("eulapkg") is not None
    finally:
        db.close()


def test_eula_decline_from_archive_helper_aborts_cleanly(tmp_path, monkeypatch):
    """Archive helper exiting 1 (decline) aborts with no on-disk deploy."""
    monkeypatch.setattr(installer_mod, "EULA_HELPER_DIR",
                        tmp_path / "no-helpers-here")
    root = _target_root(tmp_path)
    db = PackageDB(tmp_path / "t.db")
    try:
        inst = PackageInstaller(db, root=str(root))
        archive = _build_archive(tmp_path, "eulapkg2", "1.0",
                                 extra_pkginfo=["eula_helper=test-eula"],
                                 members=_eula_members(1))
        ok, msg = inst.install("eulapkg2", archive_path=archive,
                               install_reason="manual")
        assert not ok, "declined EULA must abort the install"
        assert db.get_installed("eulapkg2") is None
        assert not (root / "usr" / "lib" / "intergen").exists(), \
            "no on-disk side effects on decline"
    finally:
        db.close()


def test_eula_helper_missing_everywhere_refuses(tmp_path, monkeypatch):
    """Declared helper absent from fs AND archive → clear refusal."""
    monkeypatch.setattr(installer_mod, "EULA_HELPER_DIR",
                        tmp_path / "no-helpers-here")
    root = _target_root(tmp_path)
    db = PackageDB(tmp_path / "t.db")
    try:
        inst = PackageInstaller(db, root=str(root))
        archive = _build_archive(tmp_path, "eulapkg3", "1.0",
                                 extra_pkginfo=["eula_helper=test-eula"])
        ok, msg = inst.install("eulapkg3", archive_path=archive,
                               install_reason="manual")
        assert not ok
        assert "carries none" in msg, f"unexpected refusal text: {msg}"
    finally:
        db.close()
