#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Media on a shipped desktop: the right player opens the file and decodes in hardware.

Measured on an installed machine on 2026-09-20 (critical-surface leg CS-9 and
the desktop-session leg): two cards that decode H.264, HEVC, VP9 and AV1 in
hardware, a VA-API driver present and working, GStreamer choosing the hardware
decoder — and the shipped player still decoding in software, because mpv's own
default is hwdec=no and Celluloid passes no options. In the same session a
double-clicked WebM opened in the web browser and an MP3 in an audio editor,
because GNOME's desktop-level defaults name players this system does not ship.

Two shipped files close that, and this test pins both against the recipes:

  * packages/extra/mpv installs /etc/mpv/mpv.conf with hwdec=auto-safe (the
    conservative value: VA-API where the driver advertises the profile,
    software elsewhere; a hard "vaapi" breaks headless and some remote
    sessions) and the recipe reads the staged file back;
  * packages/core/intergenos-base-files ships the distribution-level
    /usr/share/applications/mimeapps.list naming Celluloid for video/webm and
    the common audio types (decided 2026-09-20: exactly these five).

Both files are declared in their package's verify_paths so an installed system
proves they landed.
"""
import configparser
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - the suite requires PyYAML
    pytest.skip("PyYAML not available", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
MPV_DIR = REPO_ROOT / "packages/extra/mpv"
BASE_FILES_DIR = REPO_ROOT / "packages/core/intergenos-base-files"
MIMEAPPS = BASE_FILES_DIR / "files/usr/share/applications/mimeapps.list"
CELLULOID_DESKTOP_ID = "io.github.celluloid_player.Celluloid.desktop"
DECIDED_TYPES = ("video/webm", "audio/mpeg", "audio/flac", "audio/ogg", "audio/x-wav")


def recipe(d: Path) -> dict:
    with (d / "package.yml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def uncommented(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def test_mpv_ships_a_system_config_that_turns_hardware_decoding_on():
    conf = MPV_DIR / "mpv.conf"
    assert conf.is_file(), "packages/extra/mpv/mpv.conf is missing"
    lines = [l.strip() for l in uncommented(conf.read_text(encoding="utf-8")).splitlines() if l.strip()]
    assert "hwdec=auto-safe" in lines, lines
    assert not any(l.startswith("hwdec=") and l != "hwdec=auto-safe" for l in lines), (
        "a second hwdec= line would silently override auto-safe"
    )


def test_mpv_recipe_installs_the_config_under_etc_and_reads_it_back():
    code = uncommented((MPV_DIR / "build.sh").read_text(encoding="utf-8"))
    assert 'install -Dm644 "${PKG_DIR}/mpv.conf" "${DESTDIR}/etc/mpv/mpv.conf"' in code, (
        "do_install no longer installs mpv.conf to /etc/mpv/"
    )
    assert "grep -qx 'hwdec=auto-safe' \"${DESTDIR}/etc/mpv/mpv.conf\"" in code, (
        "do_install no longer reads the staged config back"
    )
    assert "/etc/mpv/mpv.conf" in (recipe(MPV_DIR).get("verify_paths") or [])


def test_base_files_ships_the_media_default_applications():
    assert MIMEAPPS.is_file(), f"{MIMEAPPS} is missing"
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # MIME types are case-sensitive keys
    parser.read(MIMEAPPS, encoding="utf-8")
    assert parser.has_section("Default Applications"), parser.sections()
    section = parser["Default Applications"]
    for mime in DECIDED_TYPES:
        assert section.get(mime) == CELLULOID_DESKTOP_ID, (mime, section.get(mime))
    assert set(section.keys()) == set(DECIDED_TYPES), (
        "the shipped list carries types the 2026-09-20 decision did not name: "
        f"{sorted(set(section.keys()) - set(DECIDED_TYPES))}"
    )
    assert "/usr/share/applications/mimeapps.list" in (
        recipe(BASE_FILES_DIR).get("verify_paths") or []
    )


def test_the_named_player_is_the_shipped_one():
    """The desktop id must be a package this tree ships on the media.

    Celluloid's recipe does not list its desktop file among verify_paths, so
    the id is pinned against the recipe's own name and its media inclusion;
    the id string itself was read from an installed machine on 2026-09-20.
    """
    spec = recipe(REPO_ROOT / "packages/extra/celluloid")
    assert spec["name"] == "celluloid"
    assert spec.get("iso_include") is True
    assert CELLULOID_DESKTOP_ID.startswith("io.github.celluloid_player.")
