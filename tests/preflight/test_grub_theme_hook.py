"""The theme package regenerates only the running system's own boot menu.

WHAT WAS WRONG (every fresh R001.3 install, measured 2026-10-16). pkm runs a
package's sealed post_install on the host and names the root it installed into
in PKM_PACKAGE_ROOT; during a fresh install that is the target's mount point.
The hook tested the LIVE session's /boot paths instead, found the live
session's menu and ran grub-mkconfig there, which failed on the overlay root
("failed to get canonical path of `overlay'"), so pkm marked the theme package
degraded and recommended a rollback on every new machine.

WHAT THIS PROVES, running the hook exactly as it ships (sealed by the real
sealer, run with bash -e as pkm runs it) inside a mount namespace whose /boot
is a fixture, with a stand-in grub-mkconfig that records each call: a fresh
install into a target, a target that already has a menu, and the installer's
own hook phase (no PKM_PACKAGE_ROOT) regenerate nothing and succeed; on the
running system the existing menu is regenerated and a generator failure is
still reported.
"""

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BUILD_SH = REPO / "packages/desktop/intergenos-grub-theme/build.sh"
ESP_MENU = "efi/EFI/InterGenOS/grub.cfg"
BIOS_MENU = "grub/grub.cfg"


def _namespace_available():
    if shutil.which("unshare") is None:
        return False
    r = subprocess.run(["unshare", "--map-root-user", "--mount", "true"],
                       capture_output=True, timeout=30)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _namespace_available(),
                                reason="needs unshare --map-root-user --mount")


def _hookseal():
    spec = importlib.util.spec_from_file_location(
        "_hookseal_theme_test", REPO / "igos-build" / "hookseal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sealed(tmp_path):
    hs = _hookseal()
    body = hs.extract_function(BUILD_SH.read_text(), "post_install")
    script = tmp_path / "post_install.sh"
    script.write_text(hs.render_script("post_install", body,
                                       "intergenos-grub-theme", "1.0"))
    script.chmod(0o755)
    return script


def _run(tmp_path, root_env, live_menus=(), target_menus=(), generator_rc=19):
    """Run the sealed hook with /boot bound to a fixture; return (rc, out, calls)."""
    script = _sealed(tmp_path)
    boot = tmp_path / "boot"
    boot.mkdir()
    for rel in live_menus:
        (boot / rel).parent.mkdir(parents=True, exist_ok=True)
        (boot / rel).write_text("the menu before\n")
    target = tmp_path / "target"
    for rel in target_menus:
        (target / "boot" / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / "boot" / rel).write_text("the target's menu\n")
    calls = tmp_path / "calls"
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    gen = fakebin / "grub-mkconfig"
    gen.write_text(f'#!/bin/sh\necho "called:$*" >> "{calls}"\nexit {generator_rc}\n')
    gen.chmod(0o755)
    env = {"PATH": f"{fakebin}:/usr/sbin:/usr/bin:/bin", "HOME": str(tmp_path)}
    if root_env == "target":
        env["PKM_PACKAGE_ROOT"] = str(target)
    elif root_env is not None:
        env["PKM_PACKAGE_ROOT"] = root_env
    r = subprocess.run(
        ["unshare", "--map-root-user", "--mount", "/bin/bash", "-c",
         'mount --bind "$0" /boot && exec /bin/bash -e "$1"', str(boot), str(script)],
        env=env, capture_output=True, text=True, timeout=60)
    made = calls.read_text().splitlines() if calls.exists() else []
    return r.returncode, r.stdout + r.stderr, made


def test_fresh_install_into_a_target_never_touches_the_live_menu(tmp_path):
    rc, out, calls = _run(tmp_path, "target", live_menus=(ESP_MENU, BIOS_MENU))
    assert calls == [], f"grub-mkconfig ran during a fresh install: {calls}"
    assert rc == 0, out
    assert "not regenerated from here" in out


def test_a_target_that_has_a_menu_is_not_regenerated_from_the_host(tmp_path):
    rc, out, calls = _run(tmp_path, "target", target_menus=(ESP_MENU,))
    assert calls == [] and rc == 0, (calls, out)


def test_the_installer_hook_phase_leaves_the_menu_it_composed(tmp_path):
    # The installer's hook phase sources build.sh inside the target after its
    # bootloader step has composed and checked the menu; no PKM_PACKAGE_ROOT.
    rc, out, calls = _run(tmp_path, None, live_menus=(ESP_MENU,))
    assert calls == [] and rc == 0, (calls, out)


def test_the_running_system_menu_is_regenerated_and_a_failure_is_reported(tmp_path):
    rc, out, calls = _run(tmp_path, "/", live_menus=(ESP_MENU, BIOS_MENU))
    assert calls == ["called:-o /boot/efi/EFI/InterGenOS/grub.cfg"]
    assert rc == 19, out


def test_the_running_system_bios_menu_is_regenerated(tmp_path):
    rc, out, calls = _run(tmp_path, "/", live_menus=(BIOS_MENU,), generator_rc=0)
    assert calls == ["called:-o /boot/grub/grub.cfg"] and rc == 0, (calls, out)


def test_a_running_system_with_no_menu_yet_succeeds_quietly(tmp_path):
    rc, out, calls = _run(tmp_path, "/")
    assert calls == [] and rc == 0, (calls, out)
    assert "no boot menu to regenerate yet" in out
