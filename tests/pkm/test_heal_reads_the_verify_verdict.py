# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The heal judges each verify by what it says, not by its exit status alone.

scripts/redeploy-banked-archives.py --apply installs each banked archive and
then runs `pkm verify <name>`, and it counted the package healed whenever that
verify exited 0. The build root's pkm exits 0 when the package is not
installed, so a heal on 2026-10-13 reported "193/193 healed, 0 failed" while
181 of its 193 verifies had printed "Package '<name>' is not installed". The
transcript lines below are that run's own, verbatim.

A verify now counts only when it exits 0 AND prints the package's own
`ok (` verdict; "not installed" is a failure whatever the exit status says.
"""
import importlib.util
import io
import subprocess
import sys
import tarfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "redeploy_banked_archives", REPO / "scripts" / "redeploy-banked-archives.py")
heal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(heal)

NOT_INSTALLED = "  Package 'R' is not installed\n"
OK = "  ✓ crun: ok (6 files verified)\n"


def test_not_installed_with_exit_zero_is_a_failure():
    ok, why = heal.verify_verdict("R", 0, NOT_INSTALLED)
    assert not ok
    assert "not installed" in why


def test_not_installed_with_its_own_exit_code_is_a_failure():
    ok, why = heal.verify_verdict("R", 4, NOT_INSTALLED)
    assert not ok


def test_the_packages_own_ok_verdict_with_exit_zero_counts():
    ok, why = heal.verify_verdict("crun", 0, OK)
    assert ok, why


def test_an_ok_verdict_with_a_failing_exit_status_is_a_failure():
    ok, why = heal.verify_verdict("crun", 1, OK)
    assert not ok
    assert "1" in why


def test_another_packages_ok_verdict_does_not_count():
    ok, why = heal.verify_verdict("R", 0, OK)
    assert not ok


def test_no_verdict_at_all_is_a_failure():
    ok, why = heal.verify_verdict("R", 0, "")
    assert not ok


def _banked_root(tmp_path):
    chroot = tmp_path / "chroot"
    archives = chroot / "var" / "lib" / "igos" / "archives"
    archives.mkdir(parents=True)
    info = b"pkgname=R\npkgver=4.6.1\npkgrel=1\n"
    with tarfile.open(archives / "R-4.6.1.igos.tar.gz", "w:gz") as tf:
        ti = tarfile.TarInfo("./.PKGINFO")
        ti.size = len(info)
        tf.addfile(ti, io.BytesIO(info))
    recipe = tmp_path / "packages" / "extra" / "R"
    recipe.mkdir(parents=True)
    (recipe / "package.yml").write_text(
        'name: R\nversion: "4.6.1"\nrelease: 1\n')
    return chroot, tmp_path / "packages"


def test_apply_counts_a_not_installed_verify_as_a_failed_heal(tmp_path):
    chroot, packages = _banked_root(tmp_path)

    def fake_run(cmd, capture_output=True, text=True):
        verb = cmd[3]
        if verb == "install":
            return subprocess.CompletedProcess(cmd, 0, "  Installed  R 4.6.1-1\n", "")
        return subprocess.CompletedProcess(cmd, 0, NOT_INSTALLED, "")

    out = io.StringIO()
    argv = ["redeploy-banked-archives.py", "--chroot", str(chroot),
            "--packages-dir", str(packages), "--apply"]
    with patch.object(sys, "argv", argv), \
         patch.object(heal.subprocess, "run", side_effect=fake_run), \
         redirect_stdout(out), redirect_stderr(io.StringIO()):
        rc = heal.main()

    said = out.getvalue()
    assert rc == 1, said
    assert "0/1 healed, 1 failed" in said, said
    assert "not installed" in said
