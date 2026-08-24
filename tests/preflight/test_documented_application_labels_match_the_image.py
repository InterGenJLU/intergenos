# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""An application the desktop guide calls optional is not one the image ships.

WHY THIS TEST HAD TO EXIST. The desktop guide tells a reader which applications
arrive with the system and which are theirs to add. Those labels were prose,
written once and never compared against anything. Measured on an installed
R001.1 system on 2026-08-24, five applications the guide called "optional,
installed on demand" were present with install timestamps from the initial
deployment, and one of them was also listed as a default application earlier in
the same document — so the document contradicted both the machine and itself.

WHERE THE TRUTH LIVES. Not in this document and not in this test: in the
``iso_include`` field of each package's own recipe, which the build reads when
it decides what enters the squashfs. The field is resolved through the in-tree
recipe parser rather than re-read here, so the "tier extra and compute default
to excluded, every other tier defaults to included" rule has exactly one
implementation and this gate cannot drift from the build.

HOW THE GUIDE DECLARES A LABEL. A list item names its package in backticks, and
the sentence introducing the list carries one of two markers:

    These ship on the ISO and are installed by default:
    The following are optional, installed on demand:

Every backticked package under such a sentence is judged against its recipe. A
list under neither marker is not judged, which keeps the gate to the claim it
can actually check.

The negative control at the bottom proves the comparison rejects a planted
mislabel, so a green run means the labels agree rather than that nothing was
found to compare.

Nothing here writes to the tree, reads the network, or needs privilege.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUIDE = _REPO_ROOT / "docs" / "users" / "desktop-experience.md"

_SHIPS_MARKER = "ship on the ISO and are installed by default"
_OPTIONAL_MARKER = "optional, installed on demand"

_ITEM = re.compile(r"^\s*-\s+\*\*[^*]+\*\*\s+\(`([a-z0-9][a-z0-9._+-]*)`\)")

sys.path.insert(0, str(_REPO_ROOT))
_parse_template = importlib.import_module("igos-build.parser").parse_template


def _declared_labels() -> dict[str, bool]:
    """{package: expected iso_include} as the guide declares it."""
    labels: dict[str, bool] = {}
    expecting: bool | None = None
    for line in _GUIDE.read_text(encoding="utf-8").splitlines():
        if _SHIPS_MARKER in line:
            expecting = True
            continue
        if _OPTIONAL_MARKER in line:
            expecting = False
            continue
        match = _ITEM.match(line)
        if match and expecting is not None:
            labels[match.group(1)] = expecting
            continue
        if not line.strip():
            continue
        if line.startswith("#"):
            expecting = None
    return labels


def _recipe_for(package: str) -> Path | None:
    for candidate in sorted(_REPO_ROOT.glob(f"packages/*/{package}/package.yml")):
        return candidate
    return None


def _ships_in_the_image(package: str) -> bool:
    recipe = _recipe_for(package)
    assert recipe is not None, (
        f"the guide names a package `{package}` that has no recipe in this tree")
    return bool(_parse_template(recipe).iso_include)


def test_the_guide_declares_labels_for_something():
    """Positive control: an empty declaration set would pass vacuously."""
    labels = _declared_labels()
    assert labels, (
        f"no labelled application was found in {_GUIDE.name}; the comparison "
        "below would have judged nothing")
    assert True in labels.values() and False in labels.values(), (
        "the guide declares only one kind of label, so the comparison cannot "
        f"show it distinguishes them: {labels}")


def test_every_labelled_application_matches_its_recipe():
    wrong = []
    for package, expected in sorted(_declared_labels().items()):
        actual = _ships_in_the_image(package)
        if actual != expected:
            wrong.append(
                f"  `{package}` is documented as "
                f"{'shipping by default' if expected else 'optional, installed on demand'}"
                f", but its recipe resolves iso_include={actual}")
    assert not wrong, (
        "\nThe desktop guide labels an application differently from the field "
        "the build reads when it decides what enters the image:\n"
        + "\n".join(wrong)
        + "\n\nCorrect the label, or change the recipe — but the two have to "
          "say the same thing, because a reader treats the guide as the answer.")


def test_the_comparison_rejects_a_planted_mislabel(tmp_path, monkeypatch):
    """Negative control."""
    planted = {"audacity": False}   # audacity ships; claiming otherwise must fail
    monkeypatch.setattr(sys.modules[__name__], "_declared_labels",
                        lambda: planted)
    with pytest.raises(AssertionError):
        test_every_labelled_application_matches_its_recipe()
