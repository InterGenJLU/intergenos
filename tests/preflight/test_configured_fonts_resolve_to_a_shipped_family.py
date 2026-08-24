# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Every font family named in the shipped GSettings defaults is a family this
repository actually ships.

WHY THIS TEST HAD TO EXIST. The desktop defaults name a UI font, a document
font and a titlebar font. A name in that file is a REQUEST, not a guarantee:
fontconfig answers a request for a family it does not have by substituting its
best match and reporting success, so a default naming a family that is not
installed produces a desktop that renders in a different typeface with no error
anywhere. Measured on an installed R001.1 system on 2026-08-24: the configured
family resolved to a substitute, and both the release notes and the user
documentation described the requested family as the one in use.

Nothing in the build compared the two sides. The package that ships the font
files and the override file that names the family are edited independently, and
a mismatch between them is invisible until somebody looks at rendered text.

WHAT IT COMPARES, AND WHY FROM THE TREE RATHER THAN FROM THE HOST. The families
are read out of the font files this repository ships — the name table of every
TrueType/OpenType file inside each font package's asset archive — and the
requested families are parsed out of the override files. No host font is
consulted, so the result is a property of the tree and is identical on a
developer machine, in a container and on a build host, none of which need the
fonts installed.

A family that is deliberately not shipped here (one pulled from an upstream
archive at build time) is not judged: the test states which packages it read and
fails only when a REQUESTED family matches nothing in what it read, which is the
condition that produces a silent substitution.

The negative control at the bottom plants a family no font provides and proves
the comparison rejects it, so a green run above means the families agree rather
than that the test found nothing to compare.

Nothing here writes to the tree, reads the network, or needs privilege.
"""
from __future__ import annotations

import re
import struct
import tarfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OVERRIDE_DIR = _REPO_ROOT / "config" / "gsettings"
_PACKAGES = _REPO_ROOT / "packages"

# Keys whose value is a font description string ("<family> [styles] <size>").
_FONT_KEYS = ("font-name", "document-font-name", "monospace-font-name",
              "titlebar-font")

# Trailing words a font description may carry that are STYLE, not family. Taken
# from the style keywords a Pango font description accepts; matching is
# case-insensitive and only ever strips from the END of the string.
_STYLE_WORDS = {
    "thin", "ultralight", "extralight", "light", "semilight", "demilight",
    "book", "regular", "medium", "semibold", "demibold", "bold", "ultrabold",
    "extrabold", "heavy", "black", "ultraheavy", "extrablack",
    "italic", "oblique", "roman",
    "ultracondensed", "extracondensed", "condensed", "semicondensed",
    "semiexpanded", "expanded", "extraexpanded", "ultraexpanded",
    "normal", "small-caps",
}

_NAME_ID_FAMILY = 1
_NAME_ID_TYPOGRAPHIC_FAMILY = 16


def _family_of(description: str) -> str:
    """The family part of a font description string.

    ``"Inter Variable Bold 11"`` -> ``"Inter Variable"``. The size is dropped
    when the last word is a number, then style keywords are stripped from the
    end. Everything that remains is the family, including families whose own
    name ends in a word that is also a style keyword — those are protected by
    never stripping the last remaining word.
    """
    words = description.strip().split()
    if words and re.fullmatch(r"\d+(\.\d+)?", words[-1]):
        words.pop()
    while len(words) > 1 and words[-1].lower() in _STYLE_WORDS:
        words.pop()
    return " ".join(words)


def _requested_families() -> dict[str, list[str]]:
    """{family: [where it was requested]} across the shipped override files."""
    found: dict[str, list[str]] = {}
    for override in sorted(_OVERRIDE_DIR.glob("*.gschema.override")):
        for number, line in enumerate(
                override.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, raw = stripped.partition("=")
            if key.strip() not in _FONT_KEYS:
                continue
            value = raw.strip().strip("'\"")
            if not value:
                continue
            family = _family_of(value)
            found.setdefault(family, []).append(
                f"{override.name}:{number} {key.strip()}={value!r}")
    return found


def _families_in_font_file(blob: bytes) -> set[str]:
    """Family names declared in one TrueType/OpenType file's name table."""
    families: set[str] = set()
    if len(blob) < 12:
        return families
    num_tables = struct.unpack(">H", blob[4:6])[0]
    name_offset = name_length = None
    for index in range(num_tables):
        base = 12 + index * 16
        if base + 16 > len(blob):
            return families
        tag = blob[base:base + 4]
        if tag == b"name":
            name_offset, name_length = struct.unpack(">II", blob[base + 8:base + 16])
            break
    if name_offset is None or name_offset + 6 > len(blob):
        return families
    count, string_offset = struct.unpack(
        ">HH", blob[name_offset + 2:name_offset + 6])
    storage = name_offset + string_offset
    for index in range(count):
        record = name_offset + 6 + index * 12
        if record + 12 > len(blob):
            break
        platform_id, _encoding_id, _language_id, name_id, length, offset = \
            struct.unpack(">HHHHHH", blob[record:record + 12])
        if name_id not in (_NAME_ID_FAMILY, _NAME_ID_TYPOGRAPHIC_FAMILY):
            continue
        start = storage + offset
        raw = blob[start:start + length]
        if not raw:
            continue
        try:
            text = raw.decode("utf-16-be") if platform_id == 3 else raw.decode("latin-1")
        except UnicodeDecodeError:
            continue
        text = text.strip()
        if text:
            families.add(text)
    return families


def _shipped_families() -> tuple[set[str], list[str]]:
    """(families this tree ships, the archives they were read from)."""
    families: set[str] = set()
    archives: list[str] = []
    for archive in sorted(_PACKAGES.glob("*/font-*/assets/*.tar.*")):
        archives.append(str(archive.relative_to(_REPO_ROOT)))
        with tarfile.open(archive) as tar:
            for member in tar:
                if not member.isfile():
                    continue
                if not member.name.lower().endswith((".ttf", ".otf")):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                families |= _families_in_font_file(handle.read())
    return families, archives


def test_every_configured_font_family_is_one_this_tree_ships():
    requested = _requested_families()
    assert requested, (
        "no font family was found in the shipped override files; the test would "
        f"have passed without comparing anything (looked in {_OVERRIDE_DIR})")
    shipped, archives = _shipped_families()
    assert shipped, (
        "no font family could be read out of any shipped font archive, so "
        f"nothing was compared (looked for {_PACKAGES}/*/font-*/assets/*.tar.*)")

    missing = {family: where for family, where in requested.items()
               if family not in shipped}
    assert not missing, (
        "\nA GSettings default asks for a font family this repository does not "
        "ship. fontconfig answers such a request by substituting another family "
        "and reporting success, so the desktop renders in a typeface nobody "
        "chose and nothing reports it:\n"
        + "\n".join(f"  {family!r} requested at:\n" +
                    "\n".join(f"      {w}" for w in where)
                    for family, where in sorted(missing.items()))
        + "\n\n  families this tree ships: "
        + ", ".join(sorted(repr(f) for f in shipped))
        + "\n  read from: " + ", ".join(archives)
        + "\n\nEither name the shipped family in the override, or ship a font "
          "that provides the requested one.")


def test_the_comparison_rejects_a_family_nothing_provides():
    """Negative control: the comparison must detect a planted mismatch."""
    shipped, _ = _shipped_families()
    planted = "A Family No Font Provides"
    assert planted not in shipped
    assert _family_of(f"{planted} Bold 11") == planted, (
        "the family parser mangled a planted description, so the positive "
        "result above would not mean what it says")


@pytest.mark.parametrize("description,expected", [
    ("Inter 11", "Inter"),
    ("Inter Variable 11", "Inter Variable"),
    ("Inter Variable Bold 11", "Inter Variable"),
    ("JetBrains Mono 11", "JetBrains Mono"),
    ("Bold", "Bold"),                      # a family whose whole name is a style word
    ("Noto Sans Condensed Italic 10", "Noto Sans"),
])
def test_the_family_parser_splits_style_and_size_off_the_family(description, expected):
    assert _family_of(description) == expected
