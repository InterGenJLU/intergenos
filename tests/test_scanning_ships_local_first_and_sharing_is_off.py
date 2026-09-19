#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Scanning ships: driverless locally, and this machine does not offer its scanner away.

WHAT THIS COVERS. Three packages arrive together:

  * sane-backends — the library every scanning application links against and
    the drivers it drives a scanner with. It also carries saned, the daemon
    that lets OTHER machines use this machine's scanner; that daemon's socket
    listens on tcp/6566 and ships disabled, decided in the one file that
    decides such things.

  * sane-airscan — the backend that speaks the two protocols a modern scanner
    actually offers, eSCL and WSD, so a scanner works with no vendor driver.
    It is a library the SANE loader opens, not a daemon.

  * simple-scan — the application a person opens to scan.

The properties held here are that the local path needs no network daemon, that
the sharing daemon is off by default, that the driverless backend is registered
with the SANE loader rather than merely present on disk, and that the drivers
this package contains are decided by the recipe instead of by whatever happened
to be installed on the machine that built it.

WHAT THEY DO NOT PROVE: that anything scans. No scanner exists on any machine
in this project; scanning against real hardware is the R001.3 evaluation. These
tests read the tree; the delivery carries the real builds.
"""
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
PRESET = REPO / ("packages/core/intergenos-base-files/files/usr/lib/systemd/"
                 "system-preset/80-intergenos-enable.preset")
RECIPES = {
    "sane-backends": REPO / "packages/desktop/sane-backends",
    "sane-airscan": REPO / "packages/desktop/sane-airscan",
    "simple-scan": REPO / "packages/desktop/simple-scan",
}


def manifest(name):
    path = RECIPES[name] / "package.yml"
    assert path.is_file(), f"{name} has no recipe at {path}"
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_the_recipe_exists_and_pins_its_source(name):
    data = manifest(name)
    for entry in data["source"]:
        digest = entry.get("sha256", "")
        assert re.fullmatch(r"[0-9a-f]{64}", digest), (
            f"{name}: {entry.get('url')} carries no 64-character sha256 pin"
        )
        assert "${version}" in entry["url"] or data["version"] in entry["url"], (
            f"{name}: the source URL does not name the pinned version"
        )


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_the_scanning_stack_ships_on_the_media(name):
    data = manifest(name)
    assert data["tier"] == "desktop", name
    assert data.get("iso_include") is True, (
        f"{name} must be on the installed system: a scanner that works only "
        f"after a download does not work on the machine that has no network yet"
    )


def test_the_sharing_daemon_ships_disabled_and_the_preset_file_says_so():
    text = PRESET.read_text()
    assert re.search(r"^disable saned\.socket$", text, re.M), (
        "the enable-list preset file does not disable saned.socket, so whether "
        "this machine offers its scanner to the network would be decided by the "
        "absence of a line"
    )
    assert not re.search(r"^enable saned", text, re.M)


def test_the_sharing_daemons_port_is_the_one_the_decision_was_written_about():
    """The recipe must refuse a socket that listens somewhere else.

    The preset comment and this system's firewall posture were written about
    tcp/6566. If upstream moved the port, both would be describing something
    that no longer exists, and nothing would say so.
    """
    build = (RECIPES["sane-backends"] / "build.sh").read_text()
    assert "ListenStream=6566" in build, (
        "the recipe does not assert the port the sharing socket listens on"
    )


def test_the_drivers_are_decided_by_the_recipe_not_by_the_build_machine():
    """BACKENDS is pinned, and the exclusions are read back after configure.

    An unpinned configure builds whatever optional libraries the build machine
    happens to carry — measured on an authoring machine, three extra backends
    against libgphoto2, libcurl and poppler-glib that the recipe never declared.
    """
    build = (RECIPES["sane-backends"] / "build.sh").read_text()
    assert re.search(r'^\s*BACKENDS="', build, re.M), (
        "the recipe does not pin the backend list, so what the package contains "
        "depends on the machine that builds it"
    )
    for excluded in ("escl", "gphoto2", "v4l", "canon_pp", "hpsj5s",
                     "mustek_pp", "pint", "qcam"):
        assert re.search(rf"for gone in[^\n]*\b{excluded}\b", build), (
            f"{excluded} is excluded but not read back after configure; a "
            f"silently re-enabled backend would ship undeclared dependencies"
        )
    for flag in ("--without-libcurl", "--without-poppler-glib", "--without-v4l"):
        assert flag in build, (
            f"the recipe does not answer {flag} explicitly, so the optional "
            f"surface depends on the build machine"
        )


def test_the_driverless_backend_is_registered_with_the_sane_loader():
    """A backend the loader does not know about is a file on disk.

    /etc/sane.d/dll.d/airscan is what makes the loader open it; the recipe
    asserts that file's content during the build, and the manifest verifies
    both it and the library.
    """
    data = manifest("sane-airscan")
    paths = data["verify_paths"]
    assert "/etc/sane.d/dll.d/airscan" in paths
    assert "/usr/lib/sane/libsane-airscan.so.1" in paths
    build = (RECIPES["sane-airscan"] / "build.sh").read_text()
    assert "dll.d/airscan" in build and "exit 1" in build


def test_the_driverless_backend_needs_no_daemon_of_its_own():
    """It is a library the loader opens; nothing is enabled for it to work."""
    data = manifest("sane-airscan")
    assert not any(".service" in p or ".socket" in p for p in data["verify_paths"]), (
        "sane-airscan must ship no unit: it scans when an application asks, and "
        "a daemon would be a listening surface for a library's job"
    )
    for line in PRESET.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert "airscan" not in stripped, (
            f"the preset file decides something about sane-airscan: {stripped!r}"
        )


def test_the_application_scans_through_the_shipped_library():
    data = manifest("simple-scan")
    assert "sane-backends" in data["dependencies"]["runtime"], (
        "the application must declare the scanning library it runs against"
    )
    assert "/usr/bin/simple-scan" in data["verify_paths"]


def test_the_application_matches_the_desktop_this_tree_ships():
    """simple-scan 49.x, because this tree's GNOME is 49.

    A newer series builds against a platform the desktop here is not.
    """
    data = manifest("simple-scan")
    assert data["version"].startswith("49."), (
        f"simple-scan {data['version']} is not the series this desktop is; "
        f"gnome-shell in this tree is 49.x"
    )


def test_no_recipe_enables_a_scanning_daemon_behind_the_presets_back():
    for name, directory in RECIPES.items():
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                assert "systemctl enable" not in stripped, (
                    f"{name}: {path.name}:{number} enables a unit from the recipe, "
                    f"which the install-time preset pass then reverts: {stripped!r}"
                )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
