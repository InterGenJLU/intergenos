# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A directory's recorded ownership is judged the way a file's is.

When an archive's directory ownership reached a running machine's real
/usr and /usr/bin — recorded there as an ordinary account, applied because
the only check was that the directory existed — the answer was to refuse
EVERY directory. That was the right narrowing to make at the time and the
wrong rule to keep: it draws the line by member TYPE, where the file rule
draws it by what the ownership IS and whose the object is.

The line is now the same one, in two parts:

  - a directory the target ALREADY HAD belongs to the target, whoever the
    archive says owns it. That is the measured incident, and it stays
    refused — now by the fact that decides it, which is that the directory
    was not created by this deploy;
  - a directory THIS deploy created is the package's own, and its recorded
    ownership is applied when it resolves to a SYSTEM account, exactly the
    test a file member passes. An ordinary account (uid or gid at or above
    1000, the line the builder's staging chokepoint draws) is refused for a
    directory as it is for a file, and one that also carries setgid fails
    the install outright.

The discriminator is read BEFORE the extract, because afterwards every
directory the archive names exists and the two cases cannot be told apart.

THE LEGITIMATE SET, enumerated from the recipes rather than asserted: every
non-root directory ownership this tree ships is applied by a package's own
post_install hook on the live system, after the account exists — never
staged into an archive. The last test derives that set from packages/ and
holds it, so the day a recipe does stage one, this rule is what it meets
and this test says so.

The tests run unprivileged, so a real chown to a foreign id is impossible.
They record chown intent through a monkeypatch, as the sibling ownership
tests do, and assert on what the deploy path TRIED to do.
"""
from __future__ import annotations

import io
import os
import re
import tarfile
from pathlib import Path

import pytest

from pkm.database import PackageDB
from pkm.installer import PackageInstaller

REPO_ROOT = Path(__file__).resolve().parents[2]


def _archive(tmp, name, members):
    lines = [f"pkgname={name}", "pkgver=1.0", "pkgrel=1",
             "pkgdesc=test pkg", "license=GPL", "tier=core",
             "builddate=2026-09-22T00:00:00Z", "size=64", "filecount=1"]
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
    """An install root whose OWN passwd/group define the names, because the
    deploy path resolves an archive's uname against the TARGET's databases."""
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
    events = []

    def rec(path, uid, gid, **kw):
        events.append((str(path), uid, gid))

    monkeypatch.setattr(os, "chown", rec)
    return events


def _install(tmp_path, root, name, members):
    db = PackageDB(tmp_path / f"{name}.db")
    try:
        inst = PackageInstaller(db, root=str(root))
        return inst.install(name,
                            archive_path=_archive(tmp_path, name, members),
                            install_reason="manual")
    finally:
        db.close()


# --- a directory this deploy created, owned by a system account -----------

def test_a_new_directory_recorded_to_a_system_account_is_owned_by_it(
        tmp_path, monkeypatch, capsys):
    root = _target_root(tmp_path)
    events = _recording_chown(monkeypatch)

    ok, msg = _install(tmp_path, root, "svcpkg", [
        {"name": "var/lib/svcpkg/", "isdir": True, "mode": 0o750,
         "uname": "messagebus", "gname": "messagebus"},
        {"name": "var/lib/svcpkg/state", "data": b"x\n", "mode": 0o640,
         "uname": "messagebus", "gname": "messagebus"},
    ])
    assert ok, f"install failed: {msg}"
    assert (root / "var" / "lib" / "svcpkg").is_dir()

    owned = [e for e in events if e[0].rstrip("/").endswith("/var/lib/svcpkg")]
    assert owned == [(str((root / "var" / "lib" / "svcpkg").resolve()), 18, 18)], (
        f"a directory this deploy created was not given its recorded system "
        f"owner: {events}")


# --- a directory the target already had -----------------------------------

def test_a_pre_existing_directory_is_not_re_owned_even_for_a_system_account(
        tmp_path, monkeypatch, capsys):
    """The measured incident, generalised: whose the directory is decides it,
    not which account the archive names."""
    root = _target_root(tmp_path)
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "var" / "lib" / "shared").mkdir(parents=True)
    events = _recording_chown(monkeypatch)

    ok, msg = _install(tmp_path, root, "existingpkg", [
        {"name": "usr/", "isdir": True, "mode": 0o755,
         "uname": "messagebus", "gname": "messagebus"},
        {"name": "usr/bin/", "isdir": True, "mode": 0o755,
         "uname": "messagebus", "gname": "messagebus"},
        {"name": "var/lib/shared/", "isdir": True, "mode": 0o755,
         "uname": "messagebus", "gname": "messagebus"},
        {"name": "usr/bin/existingpkg-tool", "data": b"x\n", "mode": 0o755},
    ])
    assert ok, f"install failed: {msg}"

    touched = [e for e in events
               if e[0].rstrip("/").endswith(("/usr", "/usr/bin",
                                             "/var/lib/shared"))]
    assert not touched, (
        f"a directory the target already had was re-owned from an archive: "
        f"{touched}")
    err = capsys.readouterr().err
    assert "already existed on the target" in err


# --- an ordinary account, for a directory as for a file -------------------

def test_a_new_directory_recorded_to_an_ordinary_account_is_refused(
        tmp_path, monkeypatch, capsys):
    root = _target_root(tmp_path)
    events = _recording_chown(monkeypatch)

    ok, msg = _install(tmp_path, root, "leakdirpkg", [
        {"name": "opt/leakdirpkg/", "isdir": True, "mode": 0o755,
         "uname": "ordinary", "gname": "ordinary"},
        {"name": "opt/leakdirpkg/payload", "data": b"x\n", "mode": 0o644},
    ])
    assert ok, f"install failed: {msg}"
    assert (root / "opt" / "leakdirpkg").is_dir()

    touched = [e for e in events
               if e[0].rstrip("/").endswith("/opt/leakdirpkg")]
    assert not touched, (
        f"a directory in a system path was handed to an ordinary account: "
        f"{touched}")
    err = capsys.readouterr().err
    assert "ordinary" in err and "1000" in err


def test_a_setgid_directory_naming_an_ordinary_account_fails_the_install(
        tmp_path, monkeypatch):
    """A directory that passes its group to everything created inside it is
    the same escalation primitive a setuid binary is."""
    root = _target_root(tmp_path)
    _recording_chown(monkeypatch)

    ok, msg = _install(tmp_path, root, "sgiddirpkg", [
        {"name": "opt/sgiddirpkg/", "isdir": True, "mode": 0o2775,
         "uname": "ordinary", "gname": "ordinary"},
        {"name": "opt/sgiddirpkg/payload", "data": b"x\n", "mode": 0o644},
    ])
    assert not ok, "an ordinary-owned setgid directory must refuse the install"
    assert "ordinary account" in msg
    assert "opt/sgiddirpkg" in msg


def test_a_root_owned_directory_asks_for_no_chown_at_all(
        tmp_path, monkeypatch):
    """The ordinary case pays nothing: no owner recorded, no work done."""
    root = _target_root(tmp_path)
    events = _recording_chown(monkeypatch)

    ok, msg = _install(tmp_path, root, "plainpkg", [
        {"name": "opt/plainpkg/", "isdir": True, "mode": 0o755},
        {"name": "opt/plainpkg/payload", "data": b"x\n", "mode": 0o644},
    ])
    assert ok, f"install failed: {msg}"
    assert events == []


# --- the legitimate set, derived from the recipes -------------------------

_HOOK_OWNERSHIP = re.compile(
    r"\bchown\b[^|;&\n]*|\binstall\b[^|;&\n]*-o\s+\S+[^|;&\n]*")


def _recipes_setting_non_root_ownership():
    """Every recipe whose post_install applies a non-root ownership, with the
    statements it uses — derived from the tree, never listed by hand."""
    found = {}
    for build in sorted((REPO_ROOT / "packages").glob("*/*/build.sh")):
        text = build.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^post_install\(\)\s*\{(.*?)^\}", text,
                      re.S | re.M)
        if not m:
            continue
        statements = []
        for line in m.group(1).splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for hit in _HOOK_OWNERSHIP.findall(stripped):
                if re.search(r"-o\s+root\b", hit) or re.search(
                        r"chown\s+(-\S+\s+)*root:root\b", hit):
                    continue
                statements.append(hit.strip())
        if statements:
            found[build.parent.name] = statements
    return found


def test_the_tree_sets_non_root_ownership_in_hooks_and_not_in_archives():
    """The enumerated legitimate set, and where it is applied.

    Every non-root ownership this tree ships is applied by a post_install
    hook on the live system, after the account exists. None is staged into
    an archive — which is why the rule above changes nothing about what is
    installed today, and why it is stated as the rule an archive would meet
    if one ever did.
    """
    recipes = _recipes_setting_non_root_ownership()
    # The set is real, not empty — an empty derivation would make this test
    # pass by finding nothing.
    assert len(recipes) >= 5, recipes
    for expected in ("at", "fcron", "exim", "openldap"):
        assert expected in recipes, (
            f"{expected} applies a non-root ownership in its post_install "
            f"hook and the derivation did not find it: {sorted(recipes)}")
    # at's spool directories are the clearest case: created AND owned by the
    # hook, on the live system, because the archive ships no spool paths at
    # all.
    assert any("/var/spool/atjobs" in s for s in recipes["at"]), recipes["at"]


def test_no_shipped_recipe_stages_a_non_root_directory_into_its_archive():
    """The staging chokepoint's own rule, stated where a reader will look.

    A recipe that stages ownership into its archive would do it with a chown
    inside do_install (which runs against DESTDIR), not in post_install
    (which runs on the live system). None does for a directory: the builder
    forces root:root on anything staged with an id at or above 1000, and the
    remaining do_install chowns in the tree name FILES.
    """
    offenders = []
    for build in sorted((REPO_ROOT / "packages").glob("*/*/build.sh")):
        text = build.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^do_install\(\)\s*\{(.*?)^\}", text, re.S | re.M)
        if not m:
            continue
        for line in m.group(1).splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "chown" not in stripped:
                continue
            if re.search(r"chown\s+(-\S+\s+)*root:root\b", stripped):
                continue
            if "DESTDIR" in stripped and stripped.rstrip().endswith("/"):
                offenders.append((build.parent.name, stripped))
    assert offenders == [], offenders
