#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Printing ships: a USB printer works with no driver, and nothing listens by default.

WHAT THIS COVERS. Two packages arrive together:

  * ipp-usb makes a printer that speaks IPP over USB — nearly every printer
    sold since about 2016 — reachable on this machine's loopback address, so
    the print scheduler can use it with no driver and no vendor package. It has
    no enablement state at all: udev starts it when such a printer is plugged
    in and it exits when the printer is unplugged.

  * cups-browsed turns printers ADVERTISED ON THE NETWORK into local print
    destinations. It acts on what other machines say about themselves, so it
    ships off and is turned on only by the two choices the user already makes
    on the Welcomer's services page — Print Services and Network Discovery —
    and is turned off again the moment either one is turned off.

The property these tests hold is that no third switch was invented, that the
default state of the network-facing daemon is off and decided in the one file
that decides such things, and that the USB daemon does not announce anything
while the machine's own responder is off.

WHAT THEY DO NOT PROVE: that a printer prints. No IPP-over-USB device exists on
any machine in this project; printing and scanning against real hardware is the
R001.3 evaluation. These tests read the tree, and the delivery carries the real
builder's output for both recipes.
"""
import re
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
PRESET = REPO / ("packages/core/intergenos-base-files/files/usr/lib/systemd/"
                 "system-preset/80-intergenos-enable.preset")
PRIVHELPER = REPO / "assets/intergen-welcome/intergen-welcome-privhelper"
WELCOME = REPO / "assets/intergen-welcome/intergen-welcome.py"
RECIPES = {
    "ipp-usb": REPO / "packages/desktop/ipp-usb",
    "cups-browsed": REPO / "packages/desktop/cups-browsed",
}


def manifest(name):
    path = RECIPES[name] / "package.yml"
    assert path.is_file(), f"{name} has no recipe at {path}"
    return yaml.safe_load(path.read_text())


# --------------------------------------------------------- the recipes ----

@pytest.mark.parametrize("name", sorted(RECIPES))
def test_the_recipe_exists_and_pins_its_source(name):
    data = manifest(name)
    sources = data["source"]
    assert sources, f"{name} declares no source"
    for entry in sources:
        digest = entry.get("sha256", "")
        assert re.fullmatch(r"[0-9a-f]{64}", digest), (
            f"{name}: source {entry.get('url')} carries no 64-character sha256 "
            f"pin, so the build would take whatever the URL serves today"
        )
        assert "${version}" in entry["url"] or data["version"] in entry["url"], (
            f"{name}: the source URL does not name the pinned version"
        )


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_the_recipe_ships_on_the_media_in_the_desktop_tier(name):
    data = manifest(name)
    assert data["tier"] == "desktop", name
    assert data.get("iso_include") is True, (
        f"{name} must be on the installed system: a printing stack the user has "
        f"to fetch before they can print is not a printing stack that ships"
    )


def test_ipp_usb_is_started_by_udev_and_has_no_enablement_state():
    data = manifest("ipp-usb")
    paths = data["verify_paths"]
    assert "/usr/lib/udev/rules.d/71-ipp-usb.rules" in paths, (
        "the udev rule is what starts the daemon; if it is not verified, a build "
        "that failed to stage it would still pass"
    )
    assert "/usr/lib/systemd/system/ipp-usb.service" in paths

    build = (RECIPES["ipp-usb"] / "build.sh").read_text()
    assert "[Install]" in build and "exit 1" in build, (
        "the recipe must refuse a staged unit carrying an [Install] section — "
        "such a unit could be enabled and would then run at boot on a machine "
        "with no printer"
    )
    # The preset file decides units that HAVE an enablement state. This one has
    # none, so it must not appear as an enable/disable line anywhere.
    for line in PRESET.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert "ipp-usb" not in stripped, (
            f"the preset file decides ipp-usb's enablement: {stripped!r}. Its unit "
            f"has no [Install] section, so such a line cannot take effect and would "
            f"state a decision the machine does not make"
        )


def test_ipp_usb_does_not_advertise_and_does_not_leave_the_machine():
    build = (RECIPES["ipp-usb"] / "build.sh").read_text()
    assert "dns-sd *= *disable" in build, (
        "the recipe must assert that the shipped configuration does NOT advertise "
        "the printer: with the machine's mDNS responder off, upstream's default "
        "writes a publishing failure to the daemon's log every two seconds"
    )
    assert "interface *= *loopback" in build, (
        "the recipe must assert that the shipped configuration binds the loopback "
        "interface only; without that assertion an upstream default change would "
        "expose the printer to the network silently"
    )
    assert "Wants=avahi-daemon" in build, (
        "the recipe must remove upstream's Wants=avahi-daemon.service: Wants PULLS "
        "THE UNIT IN, so plugging in a printer would start the mDNS responder on a "
        "machine whose owner left Network Discovery off"
    )


# ------------------------------------------- the default state, decided ----

def test_cups_browsed_ships_disabled_and_the_preset_file_says_so():
    text = PRESET.read_text()
    assert re.search(r"^disable cups-browsed\.service$", text, re.M), (
        "the enable-list preset file does not disable cups-browsed, so its default "
        "would be the absence of a line rather than a decision"
    )
    assert not re.search(r"^enable cups-browsed", text, re.M)


def test_no_recipe_enables_either_daemon_behind_the_presets_back():
    for name, directory in RECIPES.items():
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix in (".gz", ".xz"):
                continue
            for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                assert "systemctl enable" not in stripped, (
                    f"{name}: {path.name}:{number} enables a unit from the recipe, "
                    f"which the install-time preset pass then reverts — the tree "
                    f"decides enablement in the preset file: {stripped!r}"
                )


# ------------------------------------------------- the switch wires it ----

def privhelper_verb(verb):
    """The body of one case branch of the privileged helper."""
    text = PRIVHELPER.read_text()
    start = text.index(f"    {verb})")
    end = text.index(";;", start)
    return text[start:end]


@pytest.mark.parametrize("verb", [
    "enable-printing", "disable-printing", "enable-discovery", "disable-discovery",
])
def test_every_printing_or_discovery_verb_settles_cups_browsed(verb):
    assert "sync_cups_browsed" in privhelper_verb(verb), (
        f"{verb} leaves cups-browsed in whatever state it was in, so the daemon "
        f"could keep running after the user turned one of its two halves off"
    )


def test_the_discovery_verbs_decide_whether_the_usb_printer_is_advertised():
    assert "set_ipp_usb_dnssd on" in privhelper_verb("enable-discovery")
    assert "set_ipp_usb_dnssd off" in privhelper_verb("disable-discovery")


def test_cups_browsed_is_not_a_new_switch_on_the_services_page():
    text = WELCOME.read_text()
    units = re.search(r"_SERVICE_UNITS = \{(.*?)\}", text, re.S).group(1)
    assert "cups-browsed" not in units and "ipp-usb" not in units, (
        "the services page must keep the three switches it has; both new daemons "
        "follow choices the user already makes"
    )


def test_the_helper_only_needs_the_two_units_to_decide():
    """sync_cups_browsed must derive the state, not track it.

    A helper that remembered whether it had turned the daemon on would disagree
    with the machine the first time anything else changed either unit.
    """
    text = PRIVHELPER.read_text()
    body = text[text.index("sync_cups_browsed() {"):]
    body = body[:body.index("\n}")]
    assert "unit_is_enabled cups.socket" in body
    assert "unit_is_enabled avahi-daemon.service" in body
    assert "enable --now cups-browsed.service" in body
    assert "disable --now cups-browsed.service" in body


def test_the_advertisement_switch_works_on_a_real_configuration_file():
    """Run the helper's own function, taken from the file, against a fixture.

    The function is EXTRACTED from the shipped helper rather than re-typed
    here: a re-typed regular expression proves only that the copy in the test
    works. The fixture is upstream's configuration in the shape the package
    stages it.
    """
    source = PRIVHELPER.read_text()
    start = source.index("set_ipp_usb_dnssd() {")
    end = source.index("\n}", start) + 2
    function = source[start:end]

    fixture = ("[network]\n"
               "  # Enable or disable DNS-SD advertisement\n"
               "  dns-sd = disable      # enable | disable\n"
               "  interface = loopback # all | loopback\n")

    with tempfile.TemporaryDirectory() as tmp:
        conf = Path(tmp) / "ipp-usb.conf"
        conf.write_text(fixture)
        script = (f"IPP_USB_CONF={conf}\n"
                  "systemctl() { :; }\n"          # no live unit is touched
                  f"{function}\n"
                  "set_ipp_usb_dnssd on || exit 1\n"
                  "grep -qE '^ *dns-sd *= *enable' \"$IPP_USB_CONF\" || exit 2\n"
                  "set_ipp_usb_dnssd off || exit 3\n"
                  "grep -qE '^ *dns-sd *= *disable' \"$IPP_USB_CONF\" || exit 4\n")
        result = subprocess.run(["bash", "-c", script],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, (
            f"the helper's own function did not flip the setting both ways "
            f"(exit {result.returncode}): {result.stdout}{result.stderr}"
        )
        assert "interface = loopback" in conf.read_text(), (
            "the substitution changed something other than the advertisement setting"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
