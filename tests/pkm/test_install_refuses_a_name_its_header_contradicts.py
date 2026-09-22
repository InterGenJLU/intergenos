# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An archive whose filename and sealed header name different builds is refused.

WHY (measured 2026-09-22 by a second reader). Archive names now carry their
release, and the resolvers choose by name. ``demo-1.0-11.igos.tar.gz``, a byte
copy of ``demo-1.0-7.igos.tar.gz`` whose header states release 7, installed as
"demo 1.0-7", exit 0, and nothing said the name was wrong. A name that asserts
a build nothing checks is an unverified assumption.

WHAT IS PINNED. install() reads the filename against the header's own name and
version and, by default -- for every caller -- refuses a disagreement before
anything is extracted, naming both. Under ``--archive-trust loose`` the command
line asks install() to name it and install the header's build instead. A
release-less filename agrees with any header release (it asserts none), and a
version whose upstream tail looks like a release is not mis-split.
"""
import contextlib
import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

from pkm import cli
from pkm.database import PackageDB
from pkm.installer import PackageInstaller


def _archive(tmp: Path, filename: str, name: str, version: str, release: int) -> str:
    staging = tmp / f"build-{filename}"
    (staging / "usr").mkdir(parents=True, exist_ok=True)
    (staging / "usr" / f"{name}.txt").write_text("payload\n")
    (staging / ".PKGINFO").write_text(
        f"pkgname={name}\npkgver={version}\npkgrel={release}\npkgdesc=test\n"
        f"license=GPL\ntier=core\nbuilddate=2026-09-22T00:00:00Z\nsize=8\nfilecount=1\n")
    path = tmp / filename
    with tarfile.open(path, "w:gz") as tf:
        tf.add(staging / "usr" / f"{name}.txt", arcname=f"usr/{name}.txt")
        tf.add(staging / ".PKGINFO", arcname=".PKGINFO")
    return str(path)


def _install(tmp_path, filename, name, version, release, **kw):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    db = PackageDB(tmp_path / "pkm.db")
    try:
        inst = PackageInstaller(db, root=str(root))
        archive = _archive(tmp_path, filename, name, version, release)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok, msg = inst.install(name, archive_path=archive,
                                   install_reason="manual", **kw)
        row = db.get_installed(name)
        return ok, msg, err.getvalue(), (dict(row) if row else None), root
    finally:
        db.close()


def test_a_name_that_claims_another_release_is_refused(tmp_path):
    ok, msg, _err, row, root = _install(
        tmp_path, "demo-1.0-11.igos.tar.gz", "demo", "1.0", 7)
    assert not ok, "an archive named release 11 holding release 7 was installed"
    assert "release 11" in msg and "release 7" in msg, msg
    assert "Nothing was changed" in msg, msg
    assert row is None, "the package was recorded although the install was refused"
    assert not (root / "usr" / "demo.txt").exists(), "the payload was deployed"


def test_a_name_that_claims_another_version_is_refused(tmp_path):
    ok, msg, _err, row, _root = _install(
        tmp_path, "demo-2.0-7.igos.tar.gz", "demo", "1.0", 7)
    assert not ok and row is None, msg
    assert "demo 1.0 release 7" in msg, msg


def test_loose_trust_names_the_disagreement_and_installs_the_header_build(tmp_path):
    ok, msg, err, row, _root = _install(
        tmp_path, "demo-1.0-11.igos.tar.gz", "demo", "1.0", 7,
        name_header_mismatch="report")
    assert ok, msg
    assert "release 11" in err and "release 7" in err, err
    assert row["version"] == "1.0" and row["release"] == 7, row


def test_a_name_that_agrees_with_its_header_installs(tmp_path):
    ok, msg, err, row, _root = _install(
        tmp_path, "demo-1.0-7.igos.tar.gz", "demo", "1.0", 7)
    assert ok, msg
    assert "sealed header" not in err, err
    assert row["release"] == 7


def test_a_release_less_name_agrees_with_any_header_release(tmp_path):
    ok, msg, _err, row, _root = _install(
        tmp_path, "demo-1.0.igos.tar.gz", "demo", "1.0", 7)
    assert ok, msg
    assert row["release"] == 7


def test_a_hyphenated_upstream_version_is_not_mis_split(tmp_path):
    ok, msg, _err, row, _root = _install(
        tmp_path, "imagemagick-7.1.2-13-4.igos.tar.gz", "imagemagick", "7.1.2-13", 4)
    assert ok, msg
    assert row["version"] == "7.1.2-13" and row["release"] == 4, row


def test_only_a_loose_local_archive_install_asks_for_a_report():
    mode = cli._name_header_mode
    assert mode(SimpleNamespace(archive="/x.igos.tar.gz", archive_trust="loose")) == "report"
    for trust in ("strict", "repo-only"):
        assert mode(SimpleNamespace(archive="/x.igos.tar.gz", archive_trust=trust)) == "refuse"
    assert mode(SimpleNamespace(archive=None, archive_trust="loose")) == "refuse"
    assert mode(SimpleNamespace()) == "refuse"
