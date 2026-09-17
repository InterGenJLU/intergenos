# SPDX-License-Identifier: GPL-3.0-or-later
"""One install session recognises a hook repeating itself — through the real installer.

WHY THIS FILE EXISTS.

The repeated NOTE lines that made a real install 184 lines longer are not
inside any one package operation. Measured on the R001.2-03 install trace this
machine was built from, folding identical lines within one operation folds
nothing at all: gtk-update-icon-cache says its one line once per package, for
69 packages, and update-mime-database repeats its eight-line advisory once per
package, for 9. The repeat only exists at the level of the install SESSION, and
a system install is one PackageInstaller putting 862 packages onto the target
in one process.

So the ledger belongs to that object, and this file proves it there rather than
in a helper: two real archives are installed into a real scratch root by a real
PackageInstaller, with a canonical hook that says the same thing both times.

WHAT THESE TESTS PIN.

1. The second package's install output does NOT repeat what the first already
   said, and the first's is unchanged.
2. The installer's own summary of the session names the hook, the text, the
   package it was shown for and how many operations said it.
3. Two DIFFERENT statements are both shown; folding is about repetition, not
   about volume.
4. A fresh installer starts with an empty ledger, so one install session can
   never fold on account of a previous one.
"""

from __future__ import annotations

import io
import re
import tarfile
from pathlib import Path

from pkm import hooks
from pkm.database import PackageDB
from pkm.installer import PackageInstaller


def _build_archive(tmp, name, version, payload_path):
    lines = [
        f"pkgname={name}", f"pkgver={version}", "pkgrel=1",
        "pkgdesc=note-fold test package", "license=GPL", "tier=core",
        "builddate=2026-09-16T00:00:00Z", "size=16", "filecount=1",
    ]
    archive = Path(tmp) / f"{name}-{version}.igos.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        data = ("\n".join(lines) + "\n").encode()
        info = tarfile.TarInfo("./.PKGINFO")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        payload = b"payload\n"
        info = tarfile.TarInfo(payload_path)
        info.size = len(payload)
        info.mode = 0o644
        tf.addfile(info, io.BytesIO(payload))
    return archive


def _scratch_root(tmp_path):
    root = tmp_path / "target"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
    (root / "etc" / "group").write_text("root:x:0:\n")
    return root


def _talking_hook(script):
    return hooks.CanonicalHook(
        id="icons",
        description="gtk icon cache",
        pattern=re.compile(r"^usr/share/icons/"),
        cmd_fn=lambda root, matched: ["/bin/sh", "-c", script],
        critical=False,
    )


def _install_two(tmp_path, monkeypatch, script_first, script_second):
    """Install two packages through one installer; return (messages, installer)."""
    root = _scratch_root(tmp_path)
    first = _build_archive(tmp_path, "first-icons", "1.0",
                           "usr/share/icons/hicolor/index.theme")
    second = _build_archive(tmp_path, "second-icons", "1.0",
                            "usr/share/icons/adwaita/index.theme")
    monkeypatch.setattr(hooks, "CANONICAL_HOOKS_PRE", [])
    db = PackageDB(str(root / "var" / "lib" / "igos" / "pkm.db"), root=str(root))
    messages = {}
    try:
        installer = PackageInstaller(db, root=str(root))
        monkeypatch.setattr(hooks, "CANONICAL_HOOKS", [_talking_hook(script_first)])
        ok, msg = installer.install("first-icons", archive_path=str(first))
        assert ok, f"the first install failed: {msg}"
        messages["first-icons"] = msg
        monkeypatch.setattr(hooks, "CANONICAL_HOOKS", [_talking_hook(script_second)])
        ok, msg = installer.install("second-icons", archive_path=str(second))
        assert ok, f"the second install failed: {msg}"
        messages["second-icons"] = msg
    finally:
        db.close()
    return messages, installer


SAYS_IT = "echo 'gtk-update-icon-cache: Cache file created successfully.' >&2; exit 0"


def test_the_second_package_does_not_repeat_what_the_first_said(
        tmp_path, monkeypatch):
    messages, _ = _install_two(tmp_path, monkeypatch, SAYS_IT, SAYS_IT)
    first_notes = [ln for ln in messages["first-icons"].splitlines()
                   if "] NOTE " in ln]
    second_notes = [ln for ln in messages["second-icons"].splitlines()
                    if "] NOTE " in ln]
    assert len(first_notes) == 1 and "Cache file created" in first_notes[0], (
        f"the first package did not show what its hook said: {messages!r}"
    )
    assert second_notes == [], (
        f"the second package repeated the same line: {second_notes!r}"
    )


def test_the_session_summary_says_what_was_folded(tmp_path, monkeypatch):
    _, installer = _install_two(tmp_path, monkeypatch, SAYS_IT, SAYS_IT)
    summary = installer.note_fold_summary()
    assert summary, "the installer folded a line and then said nothing about it"
    assert "icons" in summary, f"the summary does not name the hook: {summary!r}"
    assert "Cache file created" in summary, (
        f"the summary does not carry the folded text: {summary!r}"
    )
    assert "first-icons" in summary, (
        f"the summary does not say where the line was shown: {summary!r}"
    )
    folded = installer.note_fold.folded()
    assert len(folded) == 1 and folded[0].count == 2, (
        f"the session ledger did not count the repeat: {folded!r}"
    )


def test_two_different_statements_are_both_shown(tmp_path, monkeypatch):
    messages, installer = _install_two(
        tmp_path, monkeypatch,
        "echo 'the first thing' >&2; exit 0",
        "echo 'the second thing' >&2; exit 0",
    )
    assert any("the first thing" in ln
               for ln in messages["first-icons"].splitlines())
    assert any("the second thing" in ln
               for ln in messages["second-icons"].splitlines()), (
        f"a distinct statement was folded away: {messages['second-icons']!r}"
    )
    assert installer.note_fold_summary() == "", (
        "nothing repeated, yet the session claimed a fold"
    )


def test_a_fresh_installer_starts_with_an_empty_ledger(tmp_path):
    root = _scratch_root(tmp_path)
    db = PackageDB(str(root / "var" / "lib" / "igos" / "pkm.db"), root=str(root))
    try:
        installer = PackageInstaller(db, root=str(root))
        assert installer.note_fold.folded() == []
        assert installer.note_fold_summary() == ""
    finally:
        db.close()
