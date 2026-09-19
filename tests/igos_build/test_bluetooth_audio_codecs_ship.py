#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Bluetooth audio codecs a headset can actually negotiate are built in.

WHAT WENT WRONG. The audio server's recipe enabled the BlueZ backend and left
every codec option at meson's `auto`. Three of them — aptX, LDAC and LC3 — need
an encoder library that was not in this tree, so meson found nothing, disabled
them without failing, and the build reported success. A user pairing a headset
that speaks LDAC or aptX got SBC, which sounds worse, and nothing anywhere said
why. `auto` turns a missing dependency into a silently smaller product; that is
the class this file exists to close.

WHAT THE CHANGE DOES. Three recipes are added — `libfreeaptx` (the free aptX and
aptX HD encoder), `ldacBT` (the LDAC encoder with its adaptive bitrate control)
and `liblc3` (the LC3 codec) — and the audio server declares them as build and
runtime dependencies and PINS the three meson options to `enabled`. Pinned, a
missing library HALTS the configure with the dependency named, which is the
point: the codecs cannot vanish again without someone being told.

WHAT THESE TESTS PROVE. That the three options are pinned (not `auto`, not
`disabled`), that the three libraries are declared dependencies both ways, and
that each new recipe exists with a pinned sha256, a tier and verified paths.
The positive control is the pair of codec libraries the tree already carried,
`sbc` and `fdk-aac`: the same reader finds them declared, so a reader that has
stopped seeing dependencies fails its own control.

What they do NOT prove: that the codecs build or that meson enables them. Those
are proven by building the three libraries and the audio server for real and
reading meson's own configure summary, which is done once against reality and
recorded in the delivery.
"""
import pathlib
import re

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGES = REPO_ROOT / "packages"
PIPEWIRE = PACKAGES / "desktop" / "pipewire"

# option name -> the library recipe that satisfies it
CODECS = {
    "bluez5-codec-aptx": "libfreeaptx",
    "bluez5-codec-ldac": "ldacBT",
    "bluez5-codec-lc3": "liblc3",
}


def _recipe(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text())


def _configure_body() -> str:
    """The audio server's configure() function, comment lines dropped."""
    text = (PIPEWIRE / "build.sh").read_text()
    match = re.search(r"^configure\(\)\s*\{$(.*?)^\}$", text, re.S | re.M)
    assert match, "the pipewire build.sh has no configure() function to read"
    return "\n".join(
        ln for ln in match.group(1).splitlines() if not ln.strip().startswith("#")
    )


def test_the_reader_finds_the_codec_libraries_the_tree_already_carried():
    """Positive control: sbc and fdk-aac are read out of the same dependency lists."""
    recipe = _recipe(PIPEWIRE / "package.yml")
    build_deps = recipe["dependencies"]["build"]
    runtime_deps = recipe["dependencies"]["runtime"]
    for name in ("sbc", "fdk-aac"):
        assert name in build_deps and name in runtime_deps, (
            f"the control dependency {name} was not read out of the audio server's "
            "dependency lists; nothing else in this file means anything until it is"
        )


@pytest.mark.parametrize("option,library", sorted(CODECS.items()))
def test_each_codec_option_is_pinned_enabled(option, library):
    body = _configure_body()
    pinned = re.findall(rf"-D{re.escape(option)}=(\S+?)\s*\\?$", body, re.M)
    assert pinned == ["enabled"], (
        f"-D{option} must be pinned =enabled so a missing {library} halts the "
        f"configure instead of dropping the codec silently; the recipe has {pinned!r}"
    )


@pytest.mark.parametrize("option,library", sorted(CODECS.items()))
def test_each_codec_library_is_a_declared_dependency(option, library):
    recipe = _recipe(PIPEWIRE / "package.yml")
    assert library in recipe["dependencies"]["build"], (
        f"{library} satisfies -D{option} and must be a BUILD dependency of the audio "
        f"server; build deps are {recipe['dependencies']['build']!r}"
    )
    assert library in recipe["dependencies"]["runtime"], (
        f"{library} is loaded by the codec plugin at run time and must be a RUNTIME "
        f"dependency; runtime deps are {recipe['dependencies']['runtime']!r}"
    )


@pytest.mark.parametrize("library", sorted(CODECS.values()))
def test_each_codec_recipe_exists_and_pins_its_source(library):
    path = PACKAGES / "desktop" / library / "package.yml"
    assert path.is_file(), f"no recipe at {path.relative_to(REPO_ROOT)}"
    recipe = _recipe(path)
    assert recipe["name"] == library
    assert recipe["tier"] == "desktop", (
        f"{library} must be in the desktop tier, beside the audio server that uses "
        f"it; it declares {recipe['tier']!r}"
    )
    sources = recipe.get("source") or []
    assert sources, f"{library} declares no source"
    for source in sources:
        sha = source.get("sha256", "")
        assert re.fullmatch(r"[0-9a-f]{64}", sha), (
            f"{library}'s source must carry a pinned sha256; it has {sha!r}"
        )
    verify_paths = recipe.get("verify_paths") or []
    assert any(p.endswith(".so") for p in verify_paths), (
        f"{library} must verify the shipped shared library after install; "
        f"verify_paths is {verify_paths!r}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
