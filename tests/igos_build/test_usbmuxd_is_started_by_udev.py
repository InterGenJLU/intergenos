#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Apple-device multiplexing daemon is started by udev and installs no unit.

The daemon that carries traffic to an attached iPhone or iPad can be activated
two ways, and its own build system generates a DIFFERENT udev rule for each:
configured with systemd support it writes ENV{SYSTEMD_WANTS}="usbmuxd.service"
into the rule and installs a service unit; configured with --without-systemd it
writes RUN+="<sbindir>/usbmuxd --user usbmux --udev" instead and installs no
unit at all. Decided 2026-09-18: this system takes the second. The daemon then
exists only while an Apple device is plugged in — the remove rule runs
`usbmuxd -x`, which exits it when the last one is unplugged — so there is no
idle USB-facing daemon at rest and no unit anyone has to remember to disable.

The two halves of that decision are in different files and can drift apart
silently, which is what this test exists to prevent:

  * drop --without-systemd from the recipe's configure line and the build
    installs a unit AND rewrites the udev rule to want it. The package would
    still build, still install, still pass its verify_paths, and the daemon
    would now be a service — the opposite of the decision — with nothing in the
    tree objecting;
  * add an enable line for it to any preset and the always-on daemon arrives on
    every installed machine, again with nothing objecting.

A recipe comment cannot catch either. These assertions can, and they read the
recipe rather than restating it, so they stay true when the recipe is edited
for some other reason.
"""
import re
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - the suite requires PyYAML
    pytest.skip("PyYAML not available", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPE_DIR = REPO_ROOT / "packages/desktop/usbmuxd"
PACKAGE_YML = RECIPE_DIR / "package.yml"
BUILD_SH = RECIPE_DIR / "build.sh"
BASE_FILES_ENABLE_PRESET = (
    REPO_ROOT
    / "packages/core/intergenos-base-files/files/usr/lib/systemd/system-preset"
    / "80-intergenos-enable.preset"
)

UDEV_RULES_DIR = "/usr/lib/udev/rules.d"
UDEV_RULE_PATH = f"{UDEV_RULES_DIR}/39-usbmuxd.rules"
SYSTEM_UNIT_DIR = "/usr/lib/systemd/system"


def recipe() -> dict:
    """The package definition, read from the tree."""
    assert PACKAGE_YML.is_file(), f"{PACKAGE_YML} does not exist"
    with PACKAGE_YML.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def build_script() -> str:
    """The recipe's build script text, read from the tree."""
    assert BUILD_SH.is_file(), f"{BUILD_SH} does not exist"
    return BUILD_SH.read_text(encoding="utf-8")


def uncommented(text: str) -> str:
    """The script with whole-line comments removed.

    The recipe explains its activation choice at length in comments that quote
    the very flags asserted below; matching against the comments instead of the
    code would make every assertion here pass on a recipe whose actual
    configure line had been changed.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def test_recipe_exists_and_is_a_desktop_tier_package_that_ships():
    spec = recipe()
    assert spec["name"] == "usbmuxd"
    assert spec["tier"] == "desktop"
    # tier desktop already resolves to iso_include true, but the package is
    # pointless off the media: it is what makes an attached Apple device
    # visible on an installed machine, so the intent is stated, not inherited.
    assert spec.get("iso_include") is True


def test_configure_selects_udev_activation_and_no_unit_directory():
    code = uncommented(build_script())
    assert "--without-systemd" in code, (
        "the configure line no longer passes --without-systemd; the build "
        "would install a service unit and generate a udev rule that wants it"
    )
    assert f"--with-udevrulesdir={UDEV_RULES_DIR}" in code, (
        "the udev rules directory is no longer passed explicitly; the build "
        "would fall back to whatever pkg-config reports"
    )
    assert "--with-systemdsystemunitdir=no" in code, (
        "the recipe no longer refuses a unit directory outright"
    )
    assert f"--with-systemdsystemunitdir={SYSTEM_UNIT_DIR}" not in code


def test_the_recipe_ships_no_service_unit_and_no_preset():
    shipped = sorted(p.name for p in RECIPE_DIR.rglob("*") if p.is_file())
    units = [name for name in shipped if name.endswith((".service", ".socket"))]
    presets = [name for name in shipped if name.endswith(".preset")]
    assert units == [], f"the recipe carries unit files: {units}"
    assert presets == [], f"the recipe carries preset files: {presets}"

    code = uncommented(build_script())
    assert SYSTEM_UNIT_DIR not in code.replace(
        f'"$DESTDIR{SYSTEM_UNIT_DIR}"', ""
    ).replace(f"$DESTDIR{SYSTEM_UNIT_DIR}", ""), (
        "the recipe installs something into the system unit directory"
    )


def test_do_install_refuses_a_staged_unit_rather_than_trusting_the_flags():
    """The recipe checks what was STAGED, not what it asked configure for.

    A flag can be right and the outcome still wrong — an upstream change could
    install a unit from somewhere the flags do not govern. The refusal in
    do_install is the only assertion made against the actual staged tree, so it
    is pinned here against being dropped as redundant.
    """
    code = uncommented(build_script())
    assert re.search(
        r'if \[ -e "\$DESTDIR/usr/lib/systemd/system" \]', code
    ), "do_install no longer refuses a staged system unit directory"
    assert "exit 1" in code


def test_nothing_in_the_tree_enables_it_as_a_service():
    if BASE_FILES_ENABLE_PRESET.is_file():
        enable_text = BASE_FILES_ENABLE_PRESET.read_text(encoding="utf-8")
        assert "usbmuxd" not in enable_text, (
            f"{BASE_FILES_ENABLE_PRESET} enables the daemon as a service; it "
            "is started by udev and must not also be a unit"
        )
    hits = []
    for preset in REPO_ROOT.glob("packages/*/*/**/*.preset"):
        if "usbmuxd" in preset.read_text(encoding="utf-8"):
            hits.append(str(preset.relative_to(REPO_ROOT)))
    assert hits == [], f"a preset names the daemon: {hits}"


def test_verify_paths_prove_the_udev_rule_and_the_service_account():
    spec = recipe()
    declared = spec.get("verify_paths") or []
    assert UDEV_RULE_PATH in declared, (
        "the udev rule is the whole activation mechanism; if it does not land, "
        "the package installs a daemon nothing ever starts"
    )
    assert "/usr/lib/sysusers.d/usbmux.conf" in declared, (
        "the unprivileged account the daemon drops to is created from this "
        "fragment; without it the daemon has no account to become"
    )
    assert not any(path.startswith(SYSTEM_UNIT_DIR) for path in declared)
