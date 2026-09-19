# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The command that reports whether hardware video acceleration works ships on
the installation image.

WHY THIS TEST EXISTS. `vainfo`, from the `libva-utils` package, is how anyone
finds out which VA-API driver a machine has and what it can decode. It was
packaged `tier: extra` with `iso_include: false`, which made it mirror-only: the
one command that answers "is acceleration working here" had to be downloaded
first, over a network, on the machine whose graphics were in doubt. The package
was re-tiered to `desktop` on 2026-09-19 so it arrives with the system.

WHERE THE TRUTH LIVES. In the recipe, read through the in-tree parser, so the
ISO-inclusion rule ("extra, compute and toolchain default to excluded, every
other tier defaults to included") has exactly one implementation and this test
cannot drift from the build. Nothing here writes to the tree, reads the network,
or needs privilege.

WHAT WOULD MAKE THIS TEST WORTHLESS. If the helper reported "ships" for
everything, the assertions below would pass in a tree where nothing ships. The
negative control asserts the same helper reports NOT-shipped for a package the
tree deliberately keeps off the image, so a green run means the comparison
discriminates.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
_parser = importlib.import_module("igos-build.parser")
parse_template = _parser.parse_template

# The recipe is read by NAME, not by path: a later tier move changes the
# directory, and this test is about what the image carries, not about where the
# recipe file sits.
def _recipe(name: str) -> Path:
    matches = [p for p in (REPO_ROOT / "packages").rglob("package.yml")
               if p.parent.name == name]
    assert len(matches) == 1, f"expected exactly one {name} recipe, found {matches}"
    return matches[0]


def _ships_on_the_iso(name: str) -> bool:
    return bool(getattr(parse_template(_recipe(name)), "iso_include"))


def test_libva_utils_ships_on_the_iso():
    pkg = parse_template(_recipe("libva-utils"))
    assert pkg.tier == "desktop", (
        f"libva-utils is tier {pkg.tier!r}; vainfo ships because the package sits "
        f"in a tier whose ISO default is to be included")
    assert pkg.iso_include is True, (
        "libva-utils resolves to NOT shipped, so vainfo is a download away on "
        "the machine whose acceleration is in question")


def _verify_paths(name: str) -> list[str]:
    """`verify_paths` is read from the recipe text, not from the parsed Package:
    the field is registered as one the parser passes through for external
    readers (igos-build/verify_paths_derive.py), so it is not an attribute."""
    paths, capturing = [], False
    for line in _recipe(name).read_text().splitlines():
        if line.startswith("verify_paths:"):
            capturing = True
            continue
        if capturing:
            stripped = line.strip()
            if stripped.startswith("- "):
                paths.append(stripped[2:].strip())
                continue
            if stripped and not line[0].isspace():
                break
    return paths


def test_the_vainfo_binary_is_the_one_verified():
    """The claim is about a command, so the recipe must still promise that
    command — a tier move that quietly dropped the verified path would leave
    this test passing about an empty package."""
    declared = _verify_paths("libva-utils")
    assert "/usr/bin/vainfo" in declared, (
        f"libva-utils no longer verifies /usr/bin/vainfo; it declares {declared}")


def test_the_dispatcher_it_interrogates_ships_too():
    """vainfo reports on libva. A shipped diagnostic for a mirror-only library
    would report on something the image does not have."""
    assert _ships_on_the_iso("libva") is True


def test_negative_control_a_mirror_only_package_reports_not_shipped():
    """Proves the helper discriminates: amdgpu is the mirror-only meta-package
    that pulls libva-utils in on demand, and it must still read as not shipped."""
    assert _ships_on_the_iso("amdgpu") is False
