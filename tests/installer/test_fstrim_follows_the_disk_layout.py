# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Weekly discard is decided from the disk layout the installer just wrote.

Decided 2026-09-19. util-linux ships fstrim.timer, which once a week asks every
mounted filesystem to tell the drive which blocks it is no longer using. On an
ordinary unencrypted install that is worth having: a solid-state drive that is
never told keeps rewriting blocks it could have reused, and its write
performance and its endurance both suffer for it.

On an encrypted install it is not, and the reason is what the discards would
say. Which blocks a filesystem is using is a description of the filesystem, and
handing that description to the drive hands it to anyone who later reads the
drive — which is the one thing an encrypted disk exists to prevent. So the
timer is enabled on an unencrypted install and left off on an encrypted one.

A preset file cannot make this decision: it is a static list, and which layout
was written is only known while the install is running. The decision is
therefore made at install time, from the layout the partitioner returned, and
this file measures it against the real systemd preset engine and the real
`systemctl --root` the installer calls — never against a reimplementation of
either.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from installer.backend import users

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PRESET_DIR = (REPO_ROOT / "packages/core/intergenos-base-files/files"
              / "usr/lib/systemd/system-preset")

TIMER = "fstrim.timer"

# Copied from the shipped unit rather than invented: the [Install] section is
# what `systemctl enable` acts on, so a stand-in whose WantedBy differed would
# measure a different question. util-linux ships WantedBy=timers.target.
TIMER_UNIT = """[Unit]
Description=Discard unused filesystem blocks once a week
Documentation=man:fstrim

[Timer]
OnCalendar=weekly
Persistent=true

[Install]
WantedBy=timers.target
"""

SERVICE_UNIT = """[Unit]
Description=Discard unused blocks on filesystems from /etc/fstab

[Service]
Type=oneshot
ExecStart=/usr/sbin/fstrim --listed-in /etc/fstab:/proc/self/mountinfo
"""


def _scratch_root(tmp_path: Path) -> Path:
    """A filesystem root with the tree's own preset policy and the unit, in the
    state the installer leaves behind at the moment the decision is made: after
    `systemctl --root <root> preset-all` has run."""
    root = tmp_path / "root"
    (root / "usr/lib/systemd/system").mkdir(parents=True)
    (root / "usr/lib/systemd/system-preset").mkdir(parents=True)
    (root / "etc/systemd/system").mkdir(parents=True)

    for preset in sorted(PRESET_DIR.glob("*.preset")):
        shutil.copy(preset, root / "usr/lib/systemd/system-preset" / preset.name)

    (root / "usr/lib/systemd/system" / TIMER).write_text(TIMER_UNIT)
    (root / "usr/lib/systemd/system/fstrim.service").write_text(SERVICE_UNIT)

    subprocess.run(["systemctl", "--root", str(root), "preset-all"],
                   capture_output=True, text=True, check=True)
    return root


def _is_enabled(root: Path, unit: str = TIMER) -> str:
    r = subprocess.run(["systemctl", "--root", str(root), "is-enabled", unit],
                       capture_output=True, text=True)
    return r.stdout.strip()


def _wants_link(root: Path) -> Path:
    return root / "etc/systemd/system/timers.target.wants" / TIMER


class TestTheHarness:
    """Proven in both directions before anything relies on it."""

    def test_systemctl_is_available(self):
        r = subprocess.run(["systemctl", "--version"],
                           capture_output=True, text=True)
        assert r.returncode == 0, "no systemctl to resolve presets with"

    def test_the_preset_files_are_the_trees_own(self):
        names = {p.name for p in PRESET_DIR.glob("*.preset")}
        assert "99-intergenos-default-disable.preset" in names, (
            "the catch-all this test measures against is missing; a scratch "
            "root without it would report enabled for every unit and this "
            "file would pass while proving nothing")

    def test_the_catch_all_leaves_the_timer_off_before_the_decision(self, tmp_path):
        """The starting state. If this ever reads enabled, the preset policy
        already decided the question and the install-time decision below would
        be measuring its own no-op."""
        root = _scratch_root(tmp_path)
        assert _is_enabled(root) == "disabled"
        assert not _wants_link(root).exists()


class TestTheDecisionFollowsTheLayout:

    def test_an_unencrypted_install_gets_the_weekly_discard(self, tmp_path):
        root = _scratch_root(tmp_path)
        users.configure_fstrim_timer(root, luks_enabled=False)
        assert _is_enabled(root) == "enabled"
        assert _wants_link(root).is_symlink(), (
            "enabled must mean a real link under timers.target.wants, not a "
            "status string")

    def test_an_encrypted_install_does_not(self, tmp_path):
        root = _scratch_root(tmp_path)
        users.configure_fstrim_timer(root, luks_enabled=True)
        assert _is_enabled(root) == "disabled"
        assert not _wants_link(root).exists()

    def test_flipping_the_layout_flips_the_decision(self, tmp_path):
        """The positive control: the same root, the same call, one input
        changed. Without this a test that only ever saw one layout could pass
        against a function that ignored its argument."""
        unencrypted = _scratch_root(tmp_path / "a")
        encrypted = _scratch_root(tmp_path / "b")
        users.configure_fstrim_timer(unencrypted, luks_enabled=False)
        users.configure_fstrim_timer(encrypted, luks_enabled=True)
        assert _is_enabled(unencrypted) != _is_enabled(encrypted)
        assert _is_enabled(unencrypted) == "enabled"
        assert _is_enabled(encrypted) == "disabled"

    def test_the_encrypted_decision_holds_against_a_prior_enable(self, tmp_path):
        """An encrypted install must end with the timer off even if something
        earlier turned it on — the decision is made, not assumed. A function
        that only skipped its work on the encrypted path would pass every test
        above and fail this one."""
        root = _scratch_root(tmp_path)
        subprocess.run(["systemctl", "--root", str(root), "enable", TIMER],
                       capture_output=True, text=True, check=True)
        assert _is_enabled(root) == "enabled"
        users.configure_fstrim_timer(root, luks_enabled=True)
        assert _is_enabled(root) == "disabled"
        assert not _wants_link(root).exists()

    def test_it_is_idempotent_on_both_layouts(self, tmp_path):
        for luks, expected in ((False, "enabled"), (True, "disabled")):
            root = _scratch_root(tmp_path / f"run{int(luks)}")
            users.configure_fstrim_timer(root, luks_enabled=luks)
            users.configure_fstrim_timer(root, luks_enabled=luks)
            assert _is_enabled(root) == expected


class TestItFailsLoud:

    def test_a_failing_systemctl_stops_the_install(self, tmp_path):
        """A root with no unit at all: the enable cannot succeed, and an
        install that carried on would ship a machine whose discard posture
        nobody decided."""
        root = tmp_path / "empty"
        (root / "etc/systemd/system").mkdir(parents=True)
        (root / "usr/lib/systemd/system").mkdir(parents=True)
        with pytest.raises(Exception) as excinfo:
            users.configure_fstrim_timer(root, luks_enabled=False)
        assert TIMER in str(excinfo.value)


class TestTheInstallerAsksWithTheRealLayout:

    def test_the_call_site_passes_the_layout_fact(self):
        """The decision is worth nothing if the install never makes it, or
        makes it from something other than the layout that was written."""
        source = (REPO_ROOT / "installer/backend/install.py").read_text()
        assert "configure_fstrim_timer" in source, (
            "PHASE_SERVICES never asks for the decision")
        call = source.split("configure_fstrim_timer", 1)[1].split(")", 1)[0]
        assert "luks_enabled" in call, (
            "the call must pass the layout fact the partitioner returned, "
            f"not a constant; it reads: configure_fstrim_timer{call})")
