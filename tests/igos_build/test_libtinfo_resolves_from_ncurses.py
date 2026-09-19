#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The terminfo library name that third-party binaries ask for resolves on an installed system.

WHAT GOES WRONG WITHOUT THIS. A great many prebuilt binaries - anything linked
on a distribution that splits the terminal-handling library in two - carry a
dynamic dependency on `libtinfo.so.6`. They ask the loader for that exact
name. This system builds ncurses as ONE wide library: the recipe configures
without `--with-termlib`, so the terminfo entry points (`setupterm`,
`tgetent`, `tigetstr` and the rest) are exported by `libncursesw.so.6` itself
and no file named `libtinfo.so.6` is produced. The symbols are all present on
the machine; only the name is missing. A binary that wants that name fails to
start, with a loader error naming a library the user cannot install, and the
cause - a naming convention, not a missing feature - is invisible from the
error.

WHAT THE RECIPE DOES ABOUT IT. The same compatibility-symlink pattern the
recipe already uses for the non-wide names (`libncurses.so`, `libform.so`,
`libpanel.so`, `libmenu.so` and `libcurses.so`) is extended with two more
links: a versioned `libtinfo.so.6` pointing at `libncursesw.so.6`, which is
the name a prebuilt binary's dynamic section actually asks for, and an
unversioned `libtinfo.so` pointing at the versioned one, which is the name
`-ltinfo` needs at link time.

WHAT THESE TESTS PROVE, AND WHAT THEY DO NOT. They read the shipped recipe and
hold it to those two links, to the absence of `--with-termlib` (with it, the
terminfo symbols would move into a separate library and a link to
`libncursesw.so.6` would resolve to a library that no longer exports them),
and to `/usr/lib/libtinfo.so.6` being declared in `verify_paths`, so that an
install missing the link is caught by `pkm verify` rather than by a user's
failing program. They do NOT prove that the built library exports the terminfo
symbols, that the link lands on a real file, or that a real consumer runs -
those are proven by building the recipe and reading the staged tree, which is
done once against reality and recorded in the delivery, not here.

The positive control is the `libcurses.so` link that the recipe has always
carried: the same reader that must find the new links finds that one too, so a
reader that has stopped seeing link lines at all fails its own control instead
of passing this file.
"""
import pathlib
import re

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RECIPE_DIR = REPO_ROOT / "packages/core/ncurses-core"
BUILD_SH = RECIPE_DIR / "build.sh"
PACKAGE_YML = RECIPE_DIR / "package.yml"


def _do_install_body() -> str:
    """The text of the recipe's do_install function, with comment lines dropped.

    A link named only in a comment is not a link the build makes, so the
    comment lines are removed before anything is matched.
    """
    text = BUILD_SH.read_text()
    match = re.search(r"^do_install\(\)\s*\{$(.*?)^\}$", text, re.S | re.M)
    assert match, "ncurses-core build.sh has no do_install() function to read"
    lines = [ln for ln in match.group(1).splitlines() if not ln.strip().startswith("#")]
    return "\n".join(lines)


def _links_to(body: str, link_name: str):
    """Every `ln` line in body that creates <DESTDIR>/usr/lib/<link_name>, as targets."""
    pattern = re.compile(
        r"^\s*ln\s+-[a-zA-Z]+\s+(?P<target>\S+)\s+\"?\$\{?DESTDIR\}?/usr/lib/"
        + re.escape(link_name)
        + r"\"?\s*$",
        re.M,
    )
    return [m.group("target").strip('"') for m in pattern.finditer(body)]


def test_the_reader_finds_the_link_the_recipe_has_always_carried():
    """Positive control: the libcurses.so link is visible to the same reader."""
    targets = _links_to(_do_install_body(), "libcurses.so")
    assert targets == ["libncursesw.so"], (
        "the control link /usr/lib/libcurses.so -> libncursesw.so was not read out of "
        f"do_install; the reader found {targets!r}. This file's other assertions mean "
        "nothing until the control passes."
    )


def test_the_versioned_terminfo_name_links_to_the_wide_library():
    targets = _links_to(_do_install_body(), "libtinfo.so.6")
    assert targets == ["libncursesw.so.6"], (
        "the recipe must create /usr/lib/libtinfo.so.6 pointing at libncursesw.so.6, "
        "which is the name a prebuilt binary's dynamic section asks for; do_install "
        f"creates it with target(s) {targets!r}"
    )


def test_the_unversioned_terminfo_name_links_to_the_versioned_one():
    targets = _links_to(_do_install_body(), "libtinfo.so")
    assert targets == ["libtinfo.so.6"], (
        "the recipe must create /usr/lib/libtinfo.so pointing at libtinfo.so.6, the "
        f"name `-ltinfo` needs at link time; do_install creates it with {targets!r}"
    )


def test_the_recipe_still_builds_one_wide_library():
    """--with-termlib would move the terminfo symbols out of libncursesw.so.6.

    The two links above are only correct while the terminfo entry points are
    exported by the wide library itself. If the recipe ever configures with
    --with-termlib, ncurses builds a separate libtinfo of its own and these
    links must be removed, not kept.
    """
    text = BUILD_SH.read_text()
    configure_lines = [
        ln for ln in text.splitlines()
        if "--with-termlib" in ln and not ln.strip().startswith("#")
    ]
    assert configure_lines == [], (
        "ncurses-core configures with --with-termlib, so the terminfo symbols no "
        "longer live in libncursesw.so.6 and the libtinfo.so.6 compatibility link "
        f"would resolve to a library that does not export them: {configure_lines!r}"
    )


def test_the_installed_link_is_verified_after_install():
    recipe = yaml.safe_load(PACKAGE_YML.read_text())
    verify_paths = recipe.get("verify_paths") or []
    assert "/usr/lib/libtinfo.so.6" in verify_paths, (
        "/usr/lib/libtinfo.so.6 must be declared in ncurses-core's verify_paths so an "
        "install that lacks the link is caught by pkm verify rather than by a user's "
        f"failing program; verify_paths is {verify_paths!r}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
