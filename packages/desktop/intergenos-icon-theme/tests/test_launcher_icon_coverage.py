#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every visible launcher the image installs finds its icon in the InterGenOS theme.

WHY THIS TEST EXISTS. The theme covers an application by carrying a mark under
the exact name its launcher's Icon= key gives. GTK looks that name up in the
active theme first and falls back to hicolor only when the theme has nothing
under it, and hicolor is where the application's own colour icon lives. On an
R001.3 install two launchers, Document Scanner (Icon=org.gnome.SimpleScan) and
Help (Icon=org.gnome.Yelp), showed their upstream colour icons in a grid of
InterGenOS marks, because the theme carried no mark under either name, and
nothing in the tree could notice: no file here held the launchers' keys.

WHAT IT READS. launchers.tsv beside this file is the image's launcher inventory:
every .desktop file the installed image holds under /usr/share/applications,
with the Icon=, NoDisplay= and Hidden= keys of its [Desktop Entry] group and the
package that owns it, read from an installed image (its header names which and
when). The theme's names are read member by member from the bundle the recipe
installs, assets/InterGenOS.tar.gz, which build.sh pins by sha256.

WHAT IT PINS.
  * every visible launcher (NoDisplay and Hidden not true) names an icon the
    bundle carries, at any size or scalable, or sits on FORGIVEN with its reason;
  * every launcher a shipped recipe declares in its verify_paths has a row, so a
    recipe that adds a launcher to the image cannot pass unread (whether a recipe
    ships is the build parser's own effective_iso_include, imported, never
    restated);
  * every row names a package that is a recipe in this tree, so the inventory
    describes this tree's image and no other.
The negative controls at the bottom plant a visible launcher whose icon no mark
carries, a shipped recipe's launcher with no row and a row naming no recipe, and
require each check to report exactly its plant. Nothing here writes to the tree,
reads the network, or needs privilege.
"""
from __future__ import annotations

import importlib.util
import re
import tarfile
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - the suite requires PyYAML
    pytest.skip("PyYAML not available", allow_module_level=True)

HERE = Path(__file__).resolve().parent
PACKAGE_DIR = HERE.parent
REPO_ROOT = PACKAGE_DIR.parents[2]
BUNDLE = PACKAGE_DIR / "assets" / "InterGenOS.tar.gz"
INVENTORY = HERE / "launchers.tsv"
LAUNCHER_PATH = re.compile(r"^/usr/share/applications/([^/]+\.desktop)$")
MEMBER = re.compile(r"^InterGenOS/[^/]+/[^/]+/([^/]+)\.(?:png|svg)$")

# A visible launcher whose icon the theme deliberately does not carry, by its
# Icon= value, with the reason. Empty: every visible launcher of the image is
# covered.
FORGIVEN: dict[str, str] = {}


def bundle_names(bundle: Path = BUNDLE) -> set[str]:
    """The icon names the bundle carries: InterGenOS/<size or scalable>/<context>/<name>.png|svg."""
    names = set()
    with tarfile.open(bundle, "r:gz") as tar:
        for member in tar.getmembers():
            m = MEMBER.match(member.name)
            if member.isfile() and m:
                names.add(m.group(1))
    return names


def read_inventory(path: Path = INVENTORY) -> list[dict[str, str]]:
    """The inventory's rows (file, icon, nodisplay, hidden, package); comment lines start with '#'."""
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 5:
            raise ValueError("%s line %d: %d fields, 5 required" % (path.name, n, len(fields)))
        rows.append(dict(zip(("file", "icon", "nodisplay", "hidden", "package"), fields)))
    return rows


def _load_parser():
    path = REPO_ROOT / "igos-build" / "parser.py"
    spec = importlib.util.spec_from_file_location("igos_build_parser_for_launchers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def recipes(root: Path = REPO_ROOT) -> dict[str, dict]:
    """Every recipe of the tree by its package name: its tier, its effective iso_include and the launchers its
    verify_paths declare."""
    parser = _load_parser()
    out = {}
    for yml in sorted((root / "packages").glob("*/*/package.yml")):
        data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        name = data.get("name")
        if not name:
            continue
        tier = data.get("tier") or yml.parent.parent.name
        launchers = [m.group(1) for p in (data.get("verify_paths") or []) if isinstance(p, str)
                     for m in [LAUNCHER_PATH.match(p)] if m]
        out[name] = {"ships": parser.effective_iso_include(data.get("iso_include"), tier), "launchers": launchers}
    return out


def uncovered(rows, names, forgiven=FORGIVEN) -> list[str]:
    """The visible rows whose icon the bundle does not carry and FORGIVEN does not name."""
    return ["%s (Icon=%s, %s)" % (r["file"], r["icon"], r["package"]) for r in rows
            if r["nodisplay"] != "true" and r["hidden"] != "true"
            and r["icon"] not in names and r["icon"] not in forgiven]


def undeclared(rows, recipe_set) -> list[str]:
    """The launchers a shipped recipe declares that the inventory has no row for."""
    have = {r["file"] for r in rows}
    return sorted("%s (%s)" % (f, name) for name, r in recipe_set.items() if r["ships"]
                  for f in r["launchers"] if f not in have)


def foreign(rows, recipe_set) -> list[str]:
    """The rows naming a package that is no recipe of this tree."""
    return ["%s (%s)" % (r["file"], r["package"]) for r in rows if r["package"] not in recipe_set]


@pytest.fixture(scope="module")
def names():
    return bundle_names()


@pytest.fixture(scope="module")
def rows():
    return read_inventory()


@pytest.fixture(scope="module")
def recipe_set():
    return recipes()


def test_the_inventory_and_the_bundle_are_read(rows, names):
    assert len(rows) >= 50, "the inventory holds %d rows: not an image's launcher set" % len(rows)
    assert len(names) >= 250, "the bundle carries %d names: not the theme" % len(names)


def test_every_visible_launcher_finds_its_icon_in_the_theme(rows, names):
    missing = uncovered(rows, names)
    assert not missing, "visible launchers whose icon the InterGenOS theme does not carry: %s" % missing


def test_every_launcher_a_shipped_recipe_declares_has_a_row(rows, recipe_set):
    missing = undeclared(rows, recipe_set)
    assert not missing, "launchers shipped recipes declare that the inventory does not hold: %s" % missing


def test_every_row_names_a_recipe_of_this_tree(rows, recipe_set):
    stray = foreign(rows, recipe_set)
    assert not stray, "inventory rows naming no recipe of this tree: %s" % stray


def test_forgiven_names_carry_reasons_and_are_not_in_the_theme(names):
    for icon, reason in FORGIVEN.items():
        assert reason.strip(), "FORGIVEN %s carries no reason" % icon
        assert icon not in names, "FORGIVEN %s is in the theme: the entry is stale" % icon


# NEGATIVE CONTROLS: each check reports exactly its plant, read as the difference from the same check over the
# unplanted inventory, so a control holds whatever the real inventory's state.

def test_control_a_planted_visible_launcher_without_a_mark_is_reported(rows, names):
    planted = rows + [{"file": "planted.desktop", "icon": "planted-no-such-mark", "nodisplay": "false",
                       "hidden": "false", "package": "planted"}]
    base = uncovered(rows, names)
    assert [x for x in uncovered(planted, names) if x not in base] == ["planted.desktop (Icon=planted-no-such-mark, planted)"]
    hidden = rows + [{"file": "planted.desktop", "icon": "planted-no-such-mark", "nodisplay": "true",
                      "hidden": "false", "package": "planted"}]
    assert uncovered(hidden, names) == base, "a NoDisplay launcher is not on the grid"


def test_control_a_shipped_recipes_launcher_without_a_row_is_reported(rows, recipe_set):
    planted = dict(recipe_set)
    planted["planted-recipe"] = {"ships": True, "launchers": ["planted.desktop"]}
    planted["planted-mirror-only"] = {"ships": False, "launchers": ["planted-mirror.desktop"]}
    base = undeclared(rows, recipe_set)
    assert [x for x in undeclared(rows, planted) if x not in base] == ["planted.desktop (planted-recipe)"]


def test_control_a_row_naming_no_recipe_is_reported(rows, recipe_set):
    planted = rows + [{"file": "planted.desktop", "icon": "x", "nodisplay": "false", "hidden": "false",
                       "package": "no-such-recipe"}]
    base = foreign(rows, recipe_set)
    assert [x for x in foreign(planted, recipe_set) if x not in base] == ["planted.desktop (no-such-recipe)"]
