# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A configuration file only one program reads is not a system default.

The mpv package ships /etc/mpv/mpv.conf carrying hwdec=auto-safe, so that video
decodes on the card's video engine instead of on the processor. mpv reads it.
Celluloid — the player a person actually gets when they double-click a video —
ships mpv-config-enable=false in its own schema, starts libmpv with
configuration loading turned off, and never sees the file.

Measured on an installed machine on 2026-09-20 with the file in place: the
card's video engine read 0 % across six samples of a real playback through the
shipped player while the graphics engine ran 30-47 %, and 2 to 21 % when the
same player was handed the option directly. Enabling configuration loading
alone did not help — the player then reads its own configuration directory —
and enabling it together with naming /etc/mpv/mpv.conf did.

So this file pins three things that must stay true together, because any one of
them going missing restores the silent failure: the override exists and sets
both keys, the recipe installs and asserts it like its four siblings, and the
file it points at is still shipped by the package that owns it.

The schema id carries a hyphen in "celluloid-player" while the schema FILE is
named with an underscore. An override naming a schema that does not exist is
silently ineffective, so the id is asserted literally.

Nothing here reads the network, needs privilege, or writes inside the tree.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OVERRIDE = REPO_ROOT / "config" / "gsettings" / "94_intergenos-celluloid.gschema.override"
SETTINGS_BUILD = (REPO_ROOT / "packages" / "core" / "intergenos-default-settings"
                  / "build.sh")
MPV_CONF = REPO_ROOT / "packages" / "extra" / "mpv" / "mpv.conf"

# The id the schema itself declares. The hyphen is not a typo: the schema file
# is io.github.celluloid_player.Celluloid.gschema.xml, with an underscore.
SCHEMA_ID = "io.github.celluloid-player.Celluloid"
CONF_PATH = "/etc/mpv/mpv.conf"


def override_text() -> str:
    return OVERRIDE.read_text(encoding="utf-8")


def uncommented(text: str) -> str:
    """Drop comment lines so an assertion cannot be satisfied by prose."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def test_the_override_exists():
    assert OVERRIDE.is_file(), (
        f"{OVERRIDE.name} is gone; the shipped player goes back to ignoring "
        f"{CONF_PATH} and video decodes on the processor again, with nothing "
        "reporting it"
    )


def test_it_names_the_schema_the_player_actually_declares():
    body = uncommented(override_text())
    assert f"[{SCHEMA_ID}]" in body, (
        f"the override does not carry the group [{SCHEMA_ID}]. Note the hyphen "
        "in celluloid-player: the schema FILE is spelled with an underscore, "
        "and an override naming a schema that does not exist has no effect at "
        "all"
    )
    assert "[io.github.celluloid_player.Celluloid]" not in body, (
        "the override names the schema with an underscore, which is the file "
        "name rather than the schema id; it would compile and do nothing"
    )


def test_it_sets_both_keys_because_one_alone_does_not_work():
    body = uncommented(override_text())
    assert re.search(r"^mpv-config-enable=true$", body, re.M), (
        "the override no longer turns configuration loading on; the player "
        "reads no mpv configuration file at all"
    )
    assert re.search(rf"^mpv-config-file='{re.escape(CONF_PATH)}'$", body, re.M), (
        f"the override no longer names {CONF_PATH}. Turning loading on without "
        "naming the file was measured on 2026-09-20 and does NOT work: the "
        "player then looks in its own configuration directory and the card's "
        "video engine stayed at zero"
    )


def test_it_sets_defaults_rather_than_locking_them():
    body = uncommented(override_text())
    assert "lock" not in body.lower(), (
        "the override appears to lock a key; these are defaults a person may "
        "change in the player's preferences"
    )


def test_the_recipe_installs_it_like_its_siblings():
    recipe = SETTINGS_BUILD.read_text(encoding="utf-8")
    assert "94_intergenos-celluloid.gschema.override" in uncommented(recipe), (
        "the default-settings recipe does not install the override; the file "
        "would sit in the tree and reach no installed machine"
    )
    loop = re.search(r"^\s*for f in (90_intergenos.*?); do", recipe, re.M)
    assert loop, "the recipe's defensive override check is gone"
    assert "94_intergenos-celluloid" in loop.group(1), (
        "the new override is not in the recipe's defensive check, so a build "
        "that failed to stage it would still ship"
    )


def test_the_file_it_points_at_is_still_shipped():
    assert MPV_CONF.is_file(), (
        f"{MPV_CONF} is gone, so the override points the player at a file no "
        "package ships"
    )
    assert re.search(r"^hwdec=", MPV_CONF.read_text(encoding="utf-8"), re.M), (
        "the shipped mpv configuration no longer sets a decoder, so pointing "
        "the player at it achieves nothing"
    )
