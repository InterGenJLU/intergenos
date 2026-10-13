# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Execute CLI parsing, resume banners and the three bash phase dispatches."""

import os
from pathlib import Path
import re
import shlex
import subprocess

import pytest

from .test_bash_tier_skip_built import DRIVERS, function, source


def run_orchestrator(root, flags):
    text = source("build-intergenos.sh")
    parse = text[text.index('BUILD_USER=""'):text.index('if [ -z "$BUILD_USER" ]; then')]
    banner = text[text.index('BUILD_START=$(date +%s)'):text.index('# Structured trail: build_start event.')]
    script = root / "orchestrator.sh"
    script.write_text(
        "set -euo pipefail\n" + parse + "\n"
        + "log() { printf '%s\\n' \"$*\"; }\nsync_chroot_scripts() { :; }\n"
        + 'IGOS=/fixture\nSCRIPTS=/scripts\nCHECKPOINT_DIR=/checkpoints\nBUILD_LOG=/dev/null\n'
        + banner + "\n"
        + function(text, "apply_start_at_pkg") + "\n"
        + "\n".join(function(text, name) for name in ("phase_core", "phase_core_extra", "phase_base"))
        + '\nbash() { printf "DISPATCH"; printf " <%s>" "$@"; printf " START=%s\\n" "${IGOS_START_AT:-}"; }\n'
        + "phase_core\nphase_core_extra\nphase_base\n"
    )
    result = subprocess.run(["/usr/bin/bash", str(script), "--user", "builder", *flags], capture_output=True, text=True, timeout=10)
    print(f"flags={flags!r}\n{result.stdout}{result.stderr}")
    return result


@pytest.mark.parametrize("flags,enabled", [
    (["--start-at", "core"], True),
    (["--start-at", "core-extra", "--start-at-pkg", "sample"], True),
    (["--start-at", "core", "--checkpoint"], False),
    (["--checkpoint"], False),
    ([], False),
])
def test_flags_match_targeted_resume_only(tmp_path, flags, enabled):
    result = run_orchestrator(tmp_path, flags)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = "enabled" if enabled else "disabled"
    assert f"bash-tier skip-built: {expected}" in result.stdout
    dispatches = [line for line in result.stdout.splitlines() if line.startswith("DISPATCH")]
    assert len(dispatches) == 3
    assert all(("<--skip-built>" in line) == enabled for line in dispatches)
    if "--start-at-pkg" in flags:
        assert ["START=sample" in line for line in dispatches] == [False, True, False]


@pytest.mark.parametrize("driver", DRIVERS)
@pytest.mark.parametrize("flags,expected", [(["--skip-built"], "true"), ([], "false")])
def test_driver_argument_overrides_ambient_flag(tmp_path, driver, flags, expected):
    script = tmp_path / driver
    script.write_text(source(driver))
    startup = tmp_path / "startup.sh"
    startup.write_text(
        "mkdir() { :; }\n"
        "source() { printf 'FLAG=%s\\n' \"${IGOS_SKIP_BUILT:-unset}\"; exit 93; }\n"
    )
    env = {key: value for key, value in os.environ.items() if key not in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS")}
    env.update(BASH_ENV=str(startup), IGOS_START_AT="", IGOS_SKIP_BUILT="ambient")
    result = subprocess.run(["/usr/bin/bash", str(script), *flags], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 93, result.stdout + result.stderr
    assert f"FLAG={expected}" in result.stdout


def test_chroot_entry_preserves_driver_argument(tmp_path):
    # Exercise the actual entry point without entering or modifying a chroot.
    script = tmp_path / "chroot-enter.sh"
    script.write_text(source("chroot-enter.sh"))
    startup = tmp_path / "startup.sh"
    startup.write_text(
        "source() { :; }\nid() { printf '0\\n'; }\nmountpoint() { return 0; }\n"
        "chroot() { printf 'CHROOT'; printf ' <%s>' \"$@\"; printf '\\n'; }\n"
    )
    env = dict(os.environ, BASH_ENV=str(startup), IGOS=str(tmp_path), TERM="dumb")
    result = subprocess.run(["/usr/bin/bash", str(script), "/scripts/chroot-build-ch8.sh", "--skip-built"], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "</bin/bash> </scripts/chroot-build-ch8.sh> <--skip-built>" in result.stdout
