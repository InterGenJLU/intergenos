#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Apple-device multiplexing daemon is a device-triggered unit that udev wants.

The daemon that carries traffic to an attached iPhone or iPad can be activated
two ways, and its own build system generates a DIFFERENT udev rule for each:
configured with systemd support it writes ENV{SYSTEMD_WANTS}="usbmuxd.service"
into the rule and installs a service unit; configured with --without-systemd it
writes RUN+="<sbindir>/usbmuxd --user usbmux --udev" instead and installs no
unit at all.

The recipe first took the second (decided 2026-09-18) so that no unit would
exist to be enabled. Measured on an installed machine on 2026-09-20 with a real
iPhone attached, that path cannot work under systemd-udevd: udev kills every
process a RUN rule starts once the event has been handled (udev(7) says so in
as many words), so the daemon lived about five seconds and the phone never
paired. This system therefore takes the FIRST method, and keeps what the second
was chosen for by other means: upstream's unit has no [Install] section, so
nothing can enable it at boot; the only thing that starts it is the udev add
event of an Apple device; --systemd implies --enable-exit, so it exits by
itself with no device attached; and the remove rule still runs `usbmuxd -x`.

The halves of that decision are in different files and can drift apart
silently, which is what this test exists to prevent:

  * put --without-systemd back on the configure line and the build installs
    no unit and rewrites the udev rule to RUN the daemon — the killed-daemon
    shape, with nothing in the tree objecting;
  * add an enable line for it to any preset and the daemon becomes a boot-time
    service instead of a device-triggered one, again with nothing objecting;
  * drop the staged-tree checks from do_install and an upstream change to the
    unit or the rule template would ship unnoticed.

These assertions read the recipe rather than restating it, so they stay true
when the recipe is edited for some other reason.
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
UNIT_PATH = f"{SYSTEM_UNIT_DIR}/usbmuxd.service"


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


def test_configure_selects_systemd_activation_with_an_explicit_unit_directory():
    code = uncommented(build_script())
    assert "--without-systemd" not in code, (
        "the configure line passes --without-systemd again; the build would "
        "generate a udev rule that RUNs the daemon, and systemd-udevd kills a "
        "RUN-started daemon when the event ends (measured 2026-09-20)"
    )
    assert "--with-systemd " in code or code.rstrip().endswith("--with-systemd") or "--with-systemd\\" in code or re.search(r"--with-systemd\s", code), (
        "the configure line no longer asks for systemd support explicitly"
    )
    assert f"--with-systemdsystemunitdir={SYSTEM_UNIT_DIR}" in code, (
        "the unit directory is no longer passed explicitly; the build would "
        "fall back to whatever pkg-config reports on the builder"
    )
    assert "--with-systemdsystemunitdir=no" not in code
    assert "--runstatedir=/run" in code, (
        "the run-state directory is no longer passed; the unit's PIDFile= would "
        "fall back to /var/run and systemd would warn on every start"
    )
    assert f"--with-udevrulesdir={UDEV_RULES_DIR}" in code, (
        "the udev rules directory is no longer passed explicitly; the build "
        "would fall back to whatever pkg-config reports"
    )


def test_the_recipe_ships_no_unit_or_preset_of_its_own():
    """The unit is upstream's, installed by the build; the recipe adds none.

    A recipe-carried unit would be a second definition of how the daemon runs,
    and a preset would be the one way to make a device-triggered unit start at
    boot.
    """
    shipped = sorted(p.name for p in RECIPE_DIR.rglob("*") if p.is_file())
    units = [name for name in shipped if name.endswith((".service", ".socket"))]
    presets = [name for name in shipped if name.endswith(".preset")]
    assert units == [], f"the recipe carries unit files: {units}"
    assert presets == [], f"the recipe carries preset files: {presets}"


def test_do_install_asserts_the_staged_unit_and_rule_rather_than_trusting_the_flags():
    """The recipe checks what was STAGED, not what it asked configure for.

    A flag can be right and the outcome still wrong — an upstream change could
    add an [Install] section to the unit, or reword the rule template. These
    refusals in do_install are the only assertions made against the actual
    staged tree, so each is pinned here against being dropped as redundant.
    """
    code = uncommented(build_script())
    assert re.search(r'if \[ ! -f "\$_unit" \]', code), (
        "do_install no longer refuses a build that staged no service unit"
    )
    assert re.search(r"grep -q '\^\\\[Install\\\]' \"\$_unit\"", code), (
        "do_install no longer refuses a unit that carries an [Install] section"
    )
    assert 'ENV{SYSTEMD_WANTS}="usbmuxd.service"' in code, (
        "do_install no longer checks that the staged udev rule wants the unit"
    )
    assert "usbmuxd --user usbmux --udev" in code, (
        "do_install no longer refuses a udev rule that RUNs the daemon"
    )
    assert "usbmuxd -x" in code, (
        "do_install no longer checks that the remove rule exits the daemon"
    )
    assert "PIDFile=/run/usbmuxd.pid" in code, (
        "do_install no longer checks the staged unit's PIDFile= path"
    )
    assert code.count("exit 1") >= 6


def test_nothing_in_the_tree_enables_it_at_boot():
    if BASE_FILES_ENABLE_PRESET.is_file():
        enable_text = BASE_FILES_ENABLE_PRESET.read_text(encoding="utf-8")
        assert "usbmuxd" not in enable_text, (
            f"{BASE_FILES_ENABLE_PRESET} enables the daemon at boot; it is a "
            "device-triggered unit that only the udev add event may start"
        )
    hits = []
    for preset in REPO_ROOT.glob("packages/*/*/**/*.preset"):
        if "usbmuxd" in preset.read_text(encoding="utf-8"):
            hits.append(str(preset.relative_to(REPO_ROOT)))
    assert hits == [], f"a preset names the daemon: {hits}"


def test_verify_paths_prove_the_rule_the_unit_and_the_service_account():
    spec = recipe()
    declared = spec.get("verify_paths") or []
    assert UDEV_RULE_PATH in declared, (
        "the udev rule is the whole trigger; if it does not land, the package "
        "installs a daemon nothing ever starts"
    )
    assert UNIT_PATH in declared, (
        "the unit is what the rule wants; if it does not land, the udev event "
        "asks systemd for a service that does not exist"
    )
    assert "/usr/lib/sysusers.d/usbmux.conf" in declared, (
        "the unprivileged account the daemon drops to is created from this "
        "fragment; without it the daemon has no account to become"
    )
