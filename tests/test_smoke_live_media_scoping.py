# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""installer/smoke — installed-context checks must not FAIL on the live medium.

WHAT THIS PINS. The harness runs in two places: on the booted install medium
(the pre-install evaluation) and on the installed system (the post-install
evaluation). A handful of checks assert state that only an INSTALL produces.
On the medium that state is absent by design, so a FAIL there is a false one —
and a verdict with a false FAIL in it costs the reading of every other line.

Measured: the first release candidate's pre-install evaluation reported
28 PASS / 1 FAIL / 4 WARN / 11 SKIP on the booted medium, and the single FAIL
was integrity/self-reval — a check on /var/lib/igos/manifest, which the
installer writes during PHASE_CLEANUP.

Both directions are tested. A skip that fires everywhere would silence the
check on the installed system, which is the one place its subject exists — so
each test also asserts the check still runs when the boot is NOT live media.
"""

import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_DIR = REPO_ROOT / "installer" / "smoke"
LIB_SH = SMOKE_DIR / "lib.sh"
SIGNING_SH = SMOKE_DIR / "checks" / "signing.sh"
BOOT_SH = SMOKE_DIR / "checks" / "boot.sh"

LIVE_CMDLINE = ("BOOT_IMAGE=/igos-live.efi igos.mode=live "
                "igos.verity.roothash=deadbeef quiet\n")
INSTALLED_CMDLINE = ("BOOT_IMAGE=/vmlinuz-6.18.10-igos-16 "
                     "root=/dev/mapper/cryptroot rw quiet\n")


def run_check(tmp_path, check_sh, func, cmdline_text, extra=""):
    """Source lib.sh + one check module against a fixture cmdline."""
    cmdline = tmp_path / "cmdline"
    cmdline.write_text(cmdline_text)
    script = textwrap.dedent(f"""
        set -u
        SMOKE_JSON=1
        SMOKE_CMDLINE="{cmdline}"
        . "{LIB_SH}"
        . "{check_sh}"
        {extra}
        {func}
        for r in "${{SMOKE_RESULTS[@]}}"; do printf '%s\\n' "$r"; done
    """)
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [line for line in r.stdout.splitlines() if "|" in line]


def statuses(results, check_id):
    return [r.split("|")[0] for r in results if r.split("|")[1] == check_id]


class TestLiveMediaHelper:
    def test_detects_the_live_cmdline(self, tmp_path):
        cmdline = tmp_path / "cmdline"
        cmdline.write_text(LIVE_CMDLINE)
        r = subprocess.run(
            ["bash", "-c",
             f'SMOKE_CMDLINE="{cmdline}"; . "{LIB_SH}"; '
             f'smoke_live_media && echo LIVE || echo INSTALLED'],
            capture_output=True, text=True)
        assert "LIVE" in r.stdout

    def test_installed_cmdline_is_not_live(self, tmp_path):
        cmdline = tmp_path / "cmdline"
        cmdline.write_text(INSTALLED_CMDLINE)
        r = subprocess.run(
            ["bash", "-c",
             f'SMOKE_CMDLINE="{cmdline}"; . "{LIB_SH}"; '
             f'smoke_live_media && echo LIVE || echo INSTALLED'],
            capture_output=True, text=True)
        assert "INSTALLED" in r.stdout

    def test_absent_cmdline_is_not_live(self, tmp_path):
        """A missing cmdline must not read as live media — that would skip
        installed-context checks on any system whose /proc is unreadable."""
        r = subprocess.run(
            ["bash", "-c",
             f'SMOKE_CMDLINE="{tmp_path}/no-such-file"; . "{LIB_SH}"; '
             f'smoke_live_media && echo LIVE || echo INSTALLED'],
            capture_output=True, text=True)
        assert "INSTALLED" in r.stdout


class TestSelfRevalScoping:
    """integrity/self-reval — the measured false FAIL."""

    def test_skips_on_live_media(self, tmp_path):
        results = run_check(tmp_path, SIGNING_SH,
                            "check_signing_manifest_self_reval", LIVE_CMDLINE)
        assert statuses(results, "integrity/self-reval") == ["SKIP"], results

    def test_skip_states_its_reason(self, tmp_path):
        results = run_check(tmp_path, SIGNING_SH,
                            "check_signing_manifest_self_reval", LIVE_CMDLINE)
        msg = [r for r in results if r.split("|")[1] == "integrity/self-reval"][0]
        assert "live-media boot" in msg
        assert "/var/lib/igos/manifest" in msg

    def test_still_runs_on_an_installed_boot(self, tmp_path):
        """Not-live must reach the real logic — otherwise the fix has
        silenced the check everywhere instead of scoping it."""
        results = run_check(tmp_path, SIGNING_SH,
                            "check_signing_manifest_self_reval",
                            INSTALLED_CMDLINE)
        got = statuses(results, "integrity/self-reval")
        assert got, results
        msg = [r for r in results if r.split("|")[1] == "integrity/self-reval"][0]
        assert "live-media boot" not in msg


class TestBootEfiArtifactsStillScoped:
    """boot/efi-artifacts already scoped itself with an inline probe; it now
    shares the helper and must keep behaving identically."""

    def test_skips_on_live_media(self, tmp_path):
        results = run_check(tmp_path, BOOT_SH,
                            "check_boot_efi_artifacts", LIVE_CMDLINE)
        assert statuses(results, "boot/efi-artifacts") == ["SKIP"], results

    def test_runs_on_an_installed_boot(self, tmp_path):
        results = run_check(tmp_path, BOOT_SH,
                            "check_boot_efi_artifacts", INSTALLED_CMDLINE)
        got = statuses(results, "boot/efi-artifacts")
        assert got, results
        msg = [r for r in results if r.split("|")[1] == "boot/efi-artifacts"][0]
        assert "live-media boot" not in msg
