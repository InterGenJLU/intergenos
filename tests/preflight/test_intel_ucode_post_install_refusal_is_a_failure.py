# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The microcode package's post-install passes a refused boot-image signing on.

The intel-ucode post-install chains to the kernel's hook to rebuild the boot
image with the new microcode. When that hook refused to sign (its status 77,
the signing helper's MOK_REFUSED_EXIT), this script logged "WARNING: chained
kernel hook exited 77" and exited 0, so the package manager reported the
microcode update installed while the image the machine boots kept the old
microcode (found by a second read of the change that made refusals failures,
2026-10-17). The status is now passed on: 77 for a refusal, so the install is
reported not finished and the package degraded; 1 for any other failure, which
the package manager reports as a warning; 0 when the chained hook succeeded.

The SHIPPED script runs here with a stand-in for the kernel's hook (KERNEL_HOOK,
overridable for this) and its log redirected into the test's directory.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
POST_INSTALL = REPO / "packages" / "core" / "intel-ucode" / "hooks" / "post-install.sh"
HELPER = REPO / "scripts" / "mok-signing.sh"


def fire(tmp_path, kernel_hook_rc):
    # A script that cannot be pointed at the stand-in would start the real
    # kernel hook of the machine running the tests; it is refused unrun.
    assert 'KERNEL_HOOK="${KERNEL_HOOK:-' in POST_INSTALL.read_text(), (
        "the shipped script cannot be pointed at a stand-in kernel hook, so it "
        "is not run here (it would start this machine's own)")
    hook = tmp_path / "kernel-hook"
    hook.write_text("#!/bin/bash\n" + textwrap.dedent(f"""\
        echo "[linux-kernel:post-install] stand-in, exit {kernel_hook_rc}" >&2
        exit {kernel_hook_rc}
        """))
    hook.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "KERNEL_HOOK": str(hook),
           "INTEL_UCODE_HOOK_LOG": str(tmp_path / "hook.log"),
           "PKM_PACKAGE_NAME": "intel-ucode", "PKM_PACKAGE_VERSION": "20260512",
           "PKM_PACKAGE_ROOT": "/"}
    return subprocess.run(["/bin/bash", str(POST_INSTALL)], env=env,
                          capture_output=True, text=True, timeout=60)


def test_a_refused_signing_is_passed_on_as_the_refusal_status(tmp_path):
    r = fire(tmp_path, 77)
    assert r.returncode == 77, r.stderr
    assert "REFUSED: the boot image was NOT rebuilt" in r.stderr
    assert "sudo pkm reinstall linux-kernel" in r.stderr
    assert "not the MOK enrollment password" in r.stderr
    assert "WARNING" not in r.stderr


def test_another_failure_is_passed_on_as_a_failure(tmp_path):
    r = fire(tmp_path, 3)
    assert r.returncode == 1, r.stderr
    assert "WARNING: chained kernel hook exited 3" in r.stderr


def test_a_successful_rebuild_succeeds(tmp_path):
    r = fire(tmp_path, 0)
    assert r.returncode == 0, r.stderr
    assert "boot-chain refresh complete" in r.stderr


def test_the_refusal_status_is_the_signing_helpers():
    helper = HELPER.read_text()
    assert "\nMOK_REFUSED_EXIT=77\n" in helper
    assert "\nMOK_REFUSED_EXIT=77\n" in POST_INSTALL.read_text()
