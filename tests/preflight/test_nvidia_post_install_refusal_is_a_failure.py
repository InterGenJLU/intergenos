# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The NVIDIA driver's post-install asks once and fails when a signing is refused.

Measured on a real driver install on 2026-10-16 (its captured log,
09-hdmi-drop-after-driver-install.log): the kernel's hook, started by this
script to rebuild the boot image with the driver's cmdline, refused to sign;
this script logged "WARNING: UKI rebuild failed (exit 1) — nvidia cmdline
params take effect on next kernel update instead" and exited 0; the package
manager reported the driver installed, the next step said reboot, and the
machine booted an image without nvidia-drm.modeset=1, its dGPU-wired HDMI
output dead. That real firing is this test's red; the shipped script before the
change cannot be pointed at stand-ins, which is why it gained the overridable
NVIDIA_HOOK_DIR, KERNEL_HOOK and MOK_HELPER below.

The SHIPPED script runs here with stand-ins for the three things it starts (the
hardware check, the module rebuild and the kernel's hook), the REAL signing
helper, and a real encrypted key where a case needs one.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
POST_INSTALL = REPO / "packages" / "extra" / "nvidia" / "hooks" / "post-install.sh"
HELPER = REPO / "scripts" / "mok-signing.sh"
PASSPHRASE = "owner-passphrase-nvidia-1"
KVER = os.uname().release

needs_build_tree = pytest.mark.skipif(
    not Path(f"/lib/modules/{KVER}/build").is_dir(),
    reason=f"no prepared kernel build tree at /lib/modules/{KVER}/build; the "
           "script refuses before anything under test without one")


def stub(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


def make_key(directory):
    directory.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, IGOS_TEST_PASS=PASSPHRASE)
    proc = subprocess.run(
        ["openssl", "req", "-new", "-x509", "-newkey", "rsa:2048",
         "-keyout", str(directory / "mok.key"), "-out", str(directory / "mok.crt"),
         "-days", "30", "-subj", "/CN=fixture owner key/",
         "-passout", "env:IGOS_TEST_PASS"],
        env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def fire(tmp_path, *, rebuild_rc=0, kernel_hook_rc=0, with_key=False,
         supplied=None):
    hooks = tmp_path / "hooks"
    rebuild_ran = tmp_path / "rebuild.ran"
    kernel_ran = tmp_path / "kernel.ran"
    stub(hooks / "check-hardware.sh", "exit 0\n")
    stub(hooks / "rebuild-modules",
         f'env > {rebuild_ran}\nexit {rebuild_rc}\n')
    kernel_hook = stub(tmp_path / "kernel-hook",
                       f'env > {kernel_ran}\n'
                       f'echo "[linux-kernel:post-install] stand-in, exit {kernel_hook_rc}" >&2\n'
                       f'exit {kernel_hook_rc}\n')
    mok = tmp_path / "mok"
    if with_key:
        make_key(mok)
    env = {k: v for k, v in os.environ.items() if k != "IGOS_MOK_PASSPHRASE"}
    env.update({
        # No prompt program: a case that needs the owner and has no supplied
        # value has nobody to ask, which is the refusal path.
        "PATH": "/usr/bin:/bin",
        "MOK_ASK_PROGRAM": str(tmp_path / "no-such-prompt"),
        "NVIDIA_HOOK_DIR": str(hooks),
        "NVIDIA_HOOK_LOG": str(tmp_path / "nvidia-postinstall.log"),
        "KERNEL_HOOK": str(kernel_hook),
        "MOK_HELPER": str(HELPER),
        "MOK_DIR": str(mok),
        "MOK_PROTECTION_RECORD": str(tmp_path / "protection"),
        "PKM_PACKAGE_ROOT": "/",
        "PKM_PACKAGE_NAME": "nvidia",
        "PKM_PACKAGE_VERSION": "580.159.04",
    })
    if supplied is not None:
        env["IGOS_MOK_PASSPHRASE"] = supplied
    proc = subprocess.run(["/bin/bash", str(POST_INSTALL)], env=env,
                          capture_output=True, text=True, timeout=120)
    return proc, rebuild_ran, kernel_ran


@needs_build_tree
class TestTheDriverPostInstall:

    def test_a_refused_boot_image_is_a_refused_install(self, tmp_path):
        proc, _, kernel_ran = fire(tmp_path, kernel_hook_rc=77)
        out = proc.stdout + proc.stderr
        assert kernel_ran.exists(), out
        assert proc.returncode == 77, out
        assert "sudo pkm reinstall nvidia" in out
        # The package pkm marks unfinished is this one, and only its reinstall
        # clears that mark; the kernel's reinstall left it in place.
        assert "sudo pkm reinstall linux-kernel" not in out
        assert "not the MOK enrollment password" in out
        assert "Do not reboot" in out

    def test_a_built_boot_image_is_a_finished_install(self, tmp_path):
        proc, rebuild_ran, kernel_ran = fire(tmp_path)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert rebuild_ran.exists() and kernel_ran.exists()

    def test_each_step_line_is_logged_once(self, tmp_path):
        proc, _, kernel_ran = fire(tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert kernel_ran.exists()
        console = proc.stdout + proc.stderr
        log = (tmp_path / "nvidia-postinstall.log").read_text()
        steps = [line for line in console.splitlines() if "(elapsed " in line]
        assert len(steps) >= 6, console
        for step in steps:
            assert console.splitlines().count(step) == 1, step
            assert sum(line.endswith(step) for line in log.splitlines()) == 1, log
        child_line = "[linux-kernel:post-install] stand-in, exit 0"
        assert console.splitlines().count(child_line) == 1
        assert log.splitlines().count(child_line) == 1

    def test_a_refused_module_signing_stops_before_the_boot_image(self, tmp_path):
        proc, _, kernel_ran = fire(tmp_path, rebuild_rc=77)
        assert proc.returncode == 77, proc.stdout + proc.stderr
        assert not kernel_ran.exists()

    def test_no_passphrase_refuses_before_the_compile(self, tmp_path):
        proc, rebuild_ran, _ = fire(tmp_path, with_key=True)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 77, out
        assert "REFUSED" in out
        assert not rebuild_ran.exists(), "the long compile ran with no passphrase"

    def test_one_passphrase_is_held_for_every_step_it_starts(self, tmp_path):
        proc, rebuild_ran, kernel_ran = fire(tmp_path, with_key=True,
                                             supplied=PASSPHRASE)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        for ran in (rebuild_ran, kernel_ran):
            assert f"IGOS_MOK_PASSPHRASE={PASSPHRASE}" in ran.read_text().splitlines()
        assert PASSPHRASE not in proc.stdout + proc.stderr
