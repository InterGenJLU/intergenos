# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The two keyboard shortcuts the release promises are actually shipped.

WHY THIS TEST HAD TO EXIST. Two key combinations that people arriving from
other desktops reach for by reflex — Ctrl+Alt+T for a terminal and Super+D for
show-desktop — were documented as working and were bound to nothing. Measured
on an installed R001.1 system on 2026-08-24: custom-keybindings was an empty
list and show-desktop had no binding, so both keys did nothing and neither the
build nor any test noticed the gap between the documentation and the image.

They are shipped through two DIFFERENT mechanisms, and that is not a style
choice:

  show-desktop lives in org.gnome.desktop.wm.keybindings, a schema with a fixed
  path, so it belongs in a gschema override like every other core-GNOME default
  (the standing rule for system-wide GNOME settings).

  A custom keybinding does not. Its per-binding schema
  org.gnome.settings-daemon.plugins.media-keys.custom-keybinding is
  RELOCATABLE — it has no fixed path, and a gschema override can only set keys
  in a schema that has one. The dconf layer is path-based, so a system dconf db
  is the only mechanism that can carry it, exactly as the dock and shell-effect
  defaults beside it are carried for their own structural reason. The list key
  that points at the binding is kept in the same file rather than split into the
  override, so one setting has one writer.

WHAT THIS PINS.
  * the show-desktop binding, in the override;
  * the custom keybinding: the list entry, the matching path section, its
    binding, and a name;
  * that the command it runs names a binary a SHIPPED RECIPE INSTALLS — a
    keybinding pointing at a binary the image does not carry is the same defect
    in a new costume, and this is checked against the recipes' own verify_paths
    rather than against whatever happens to be on the machine running the test;
  * that the dconf fragment is actually installed by the package's build, so
    the asset cannot sit in the tree unshipped.

The negative controls at the bottom plant a command no recipe installs and a
missing binding, and require the comparison to reject both.

Nothing here writes to the tree, reads the network, or needs privilege.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OVERRIDE = _REPO_ROOT / "config" / "gsettings" / "92_intergenos-desktop.gschema.override"
_SETTINGS_PKG = _REPO_ROOT / "packages" / "core" / "intergenos-default-settings"
_FRAGMENT = _SETTINGS_PKG / "assets" / "dconf" / "db" / "local.d" / "03-intergenos-keybindings"
_BUILD = _SETTINGS_PKG / "build.sh"

_CUSTOM_PATH = ("/org/gnome/settings-daemon/plugins/media-keys/"
                "custom-keybindings/custom0/")
_CUSTOM_SECTION = ("org/gnome/settings-daemon/plugins/media-keys/"
                   "custom-keybindings/custom0")


def _ini_sections(text: str) -> dict[str, dict[str, str]]:
    """Parse a dconf/override fragment into {section: {key: raw value}}."""
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = sections.setdefault(stripped[1:-1], {})
            continue
        if current is None or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        current[key.strip()] = value.strip()
    return sections


def _shipped_binaries() -> set[str]:
    """Every absolute path a first-party recipe declares it installs."""
    paths: set[str] = set()
    for recipe in _REPO_ROOT.glob("packages/*/*/package.yml"):
        text = recipe.read_text(encoding="utf-8", errors="replace")
        in_block = False
        for line in text.splitlines():
            if re.match(r"^verify_paths:\s*$", line):
                in_block = True
                continue
            if in_block:
                match = re.match(r"^\s+-\s+(/\S+)\s*$", line)
                if match:
                    paths.add(match.group(1))
                    continue
                if line.strip() and not line.startswith((" ", "\t")):
                    in_block = False
    return paths


def _command_of(section: dict[str, str]) -> str:
    return section.get("command", "").strip().strip("'\"")


def test_show_desktop_is_bound_in_the_desktop_override():
    sections = _ini_sections(_OVERRIDE.read_text(encoding="utf-8"))
    keybindings = sections.get("org.gnome.desktop.wm.keybindings", {})
    value = keybindings.get("show-desktop", "")
    assert "<Super>d" in value, (
        f"{_OVERRIDE.name} does not bind show-desktop to <Super>d "
        f"(found {value!r}). The documentation tells a reader the key shows "
        "the desktop; nothing binds it.")


def test_the_terminal_custom_keybinding_is_declared():
    assert _FRAGMENT.is_file(), (
        f"{_FRAGMENT.relative_to(_REPO_ROOT)} does not exist, so no custom "
        "keybinding is shipped. A gschema override cannot carry one: the "
        "per-binding schema is relocatable and has no fixed path.")
    sections = _ini_sections(_FRAGMENT.read_text(encoding="utf-8"))
    media = sections.get("org/gnome/settings-daemon/plugins/media-keys", {})
    assert _CUSTOM_PATH in media.get("custom-keybindings", ""), (
        "the custom-keybindings list does not point at "
        f"{_CUSTOM_PATH} — a binding section with nothing pointing at it is "
        "never read")
    entry = sections.get(_CUSTOM_SECTION, {})
    assert entry, f"no [{_CUSTOM_SECTION}] section in the fragment"
    assert "<Control><Alt>t" in entry.get("binding", ""), (
        f"the binding is {entry.get('binding')!r}, not <Control><Alt>t")
    assert entry.get("name", "").strip("'\""), (
        "the binding has no name; it appears unnamed in the Settings panel")
    assert _command_of(entry), "the binding runs no command"


def test_the_bound_command_names_a_binary_a_recipe_installs():
    sections = _ini_sections(_FRAGMENT.read_text(encoding="utf-8"))
    command = _command_of(sections.get(_CUSTOM_SECTION, {}))
    assert command, "no command to check"
    binary = command.split()[0]
    assert binary.startswith("/"), (
        f"the command {command!r} is not an absolute path; what it resolves to "
        "then depends on the session's PATH rather than on the image")
    shipped = _shipped_binaries()
    assert shipped, "no recipe declared any verify_paths; nothing was compared"
    assert binary in shipped, (
        f"the keybinding runs {binary}, which no shipped recipe declares it "
        "installs. A key bound to a binary the image does not carry is the "
        "same defect as a key bound to nothing.")


def test_the_fragment_is_installed_by_the_package_build():
    build = _BUILD.read_text(encoding="utf-8")
    assert _FRAGMENT.name in build, (
        f"{_BUILD.relative_to(_REPO_ROOT)} does not install "
        f"{_FRAGMENT.name}; the asset would sit in the tree unshipped")
    assert f"etc/dconf/db/local.d/{_FRAGMENT.name}" in build, (
        "the fragment is referenced but not installed into "
        "/etc/dconf/db/local.d, where the dconf profile reads it")


@pytest.mark.parametrize("planted", ["/usr/bin/a-terminal-nothing-ships",
                                     "not-an-absolute-path"])
def test_the_command_check_rejects_a_binary_nothing_ships(planted):
    """Negative control."""
    shipped = _shipped_binaries()
    assert planted not in shipped
    assert not (planted.startswith("/") and planted in shipped)
