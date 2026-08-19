# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M-002 gate: a required binary nobody claims now fails the build.

WHAT CHANGED. scripts/check-installer-runtime-deps.py has always reported two
classes: MISSING (the installer calls a binary the chroot does not have) and
UNOWNED (the binary is there, but no package.yml verify_paths entry claims the
path it is at). MISSING failed the build; UNOWNED printed and passed. The
first release was built with 29 UNOWNED — 29 binaries the installer depends on
that the pre-squashfs verify_paths audit never checked had landed, because no
package declared them.

All of them are declared now, so scripts/build-squashfs.sh Step 4.4 passes
--strict-unowned and the zero holds by gate rather than by attention.

WHAT THESE TESTS CAN AND CANNOT PROVE. They prove the flag's behaviour in both
directions and that every required binary is claimed at a standard search path
in this tree. They cannot prove the chroot puts each binary at the path the
tree claims — that is a property of a built chroot, and the next build is
where it is measured.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "scripts" / "check-installer-runtime-deps.py"


def load_gate():
    spec = importlib.util.spec_from_file_location("m002", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = load_gate()
REQUIRED = sorted(M.scan_python(REPO_ROOT / "installer") | M.SHELL_REQUIRED_BINARIES)
OWNERS = M.collect_verify_paths(REPO_ROOT)


def claimed_path(binary):
    """The standard-search-dir path some package.yml claims for this binary."""
    for d in M.CHROOT_BIN_DIRS:
        p = f"/{d}/{binary}"
        if p in OWNERS:
            return p
    return None


def build_chroot(tmp_path, binaries, name="chroot"):
    root = tmp_path / name
    for b in binaries:
        p = claimed_path(b) or f"/usr/bin/{b}"
        f = root / p.lstrip("/")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("#!/bin/sh\n")
    return root


def run_gate(chroot, project, strict):
    cmd = [sys.executable, str(GATE), "--chroot", str(chroot), "--project", str(project)]
    if strict:
        cmd.append("--strict-unowned")
    return subprocess.run(cmd, capture_output=True, text=True)


class TestTreeState:
    def test_every_required_binary_is_claimed(self):
        """The condition the flag depends on, stated as a test rather than as
        a one-off measurement."""
        unclaimed = [b for b in REQUIRED if claimed_path(b) is None]
        assert unclaimed == [], f"{len(unclaimed)} required binaries claimed by no package: {unclaimed}"

    def test_gate_passes_strict_against_a_fully_populated_chroot(self, tmp_path):
        chroot = build_chroot(tmp_path, REQUIRED)
        r = run_gate(chroot, REPO_ROOT, strict=True)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "0 unowned" in r.stdout, r.stdout


class TestFlagBehaviour:
    """A gate never shown to detect a true positive cannot certify a zero."""

    @pytest.fixture
    def unclaiming_project(self, tmp_path):
        """A project whose packages/ claims nothing — every found binary is
        UNOWNED."""
        proj = tmp_path / "project"
        (proj / "packages" / "core" / "thing").mkdir(parents=True)
        (proj / "packages" / "core" / "thing" / "package.yml").write_text(
            "name: thing\nverify_paths:\n  - /usr/lib/libthing.so\n")
        (proj / "installer").mkdir()
        return proj

    def test_strict_fails_on_an_unclaimed_binary(self, tmp_path, unclaiming_project):
        chroot = build_chroot(tmp_path, sorted(M.SHELL_REQUIRED_BINARIES), name="c1")
        r = run_gate(chroot, unclaiming_project, strict=True)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "UNOWNED binaries (strict mode)" in r.stderr

    def test_without_the_flag_the_same_state_passes(self, tmp_path, unclaiming_project):
        chroot = build_chroot(tmp_path, sorted(M.SHELL_REQUIRED_BINARIES), name="c2")
        r = run_gate(chroot, unclaiming_project, strict=False)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_a_missing_binary_still_fails_either_way(self, tmp_path):
        chroot = build_chroot(tmp_path, REQUIRED[1:], name="c3")
        for strict in (True, False):
            r = run_gate(chroot, REPO_ROOT, strict=strict)
            assert r.returncode == 1, (strict, r.stdout + r.stderr)
            assert "MISSING IN CHROOT" in r.stderr


class TestCallSite:
    def test_build_squashfs_passes_the_flag(self):
        text = (REPO_ROOT / "scripts" / "build-squashfs.sh").read_text()
        assert '--strict-unowned 2>&1' in text
