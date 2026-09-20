#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The desktop's user help has CONTENT, and the content matches the desktop.

A help viewer with nothing to show and no viewer at all fail the same way in
front of a person: the Help menu item opens a window that says the page cannot
be found, or opens nothing. This tree ships the viewer (asserted next door in
test_help_viewer_ships.py) and the pages it displays, and only the pair is
useful. This file asserts the pages: the documentation package is in the tree,
in the tier that reaches an installed machine, pinned by digest, verified after
install at the directory the viewer reads, and built with the toolchain that
turns its Mallard sources into installed pages.

WHY THE VERSION IS TIED TO THE SHELL AND NOT LEFT FREE. The user guide
describes a specific desktop: its screenshots, its menu names and its settings
panels are the ones that release shipped. Documentation a major version away
from the running shell tells a person to click something that is not there,
which is worse than no documentation, because it is confidently wrong. The
assertion below pins the guide's major version to the shell's, so a shell bump
that leaves the documentation behind fails here instead of in front of a user.

WHY THE BUILD TOOLCHAIN IS ASSERTED. The sources are Mallard XML; `itstool`
and `libxml2` are what turn them into the installed pages. Without them
declared, the recipe builds on a machine that happens to have them and fails in
the clean build chroot, which is the expensive place to find out.

The final test is a POSITIVE CONTROL. Every predicate here is also applied to a
desktop package this tree already ships, so a failure above is evidence about
the user help rather than about a predicate that could never pass.
"""
import re
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - the suite requires PyYAML
    pytest.skip("PyYAML not available", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPE_DIR = REPO_ROOT / "packages/desktop/gnome-user-docs"
PACKAGE_YML = RECIPE_DIR / "package.yml"

# The directory the help viewer reads its pages from. yelp resolves
# `help:gnome-help` by looking here; an empty or absent directory is the
# silent failure this file exists to prevent.
HELP_DIR = "/usr/share/help"

# The shell whose release the documentation describes.
SHELL_PACKAGE_YML = REPO_ROOT / "packages/desktop/gnome-shell/package.yml"
# The viewer that displays these pages. Content with no viewer is unreachable.
VIEWER_PACKAGE_YML = REPO_ROOT / "packages/desktop/yelp/package.yml"

# The Mallard toolchain that produces the installed pages from the sources.
DOC_BUILD_TOOLS = ("itstool", "libxml2")

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# The control: a desktop package this tree shipped before this file existed.
CONTROL_PACKAGE_YML = REPO_ROOT / "packages/desktop/evince/package.yml"
CONTROL_VERIFY_PATH = "/usr/bin/evince"


def read_recipe(path: Path) -> dict:
    """The package definition at `path`, read from the tree."""
    assert path.is_file(), f"{path} does not exist"
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def recipe() -> dict:
    return read_recipe(PACKAGE_YML)


def major(version) -> str:
    """The leading numeric component of a version string."""
    return str(version).split(".")[0]


def test_the_user_help_has_a_recipe_in_the_desktop_tier():
    """The pages are packaged, and in the tier that reaches an installed desktop.

    `desktop` is the tier whose members are on the installation image by
    default. The guide in `extra` would be a guide nobody has when they open
    Help, and the viewer would open on an error page.
    """
    assert RECIPE_DIR.is_dir(), (
        f"{RECIPE_DIR} does not exist: the desktop ships a help viewer with no "
        "pages, so Help opens on a page-not-found instead of the user guide"
    )
    data = recipe()
    assert data.get("name") == "gnome-user-docs"
    assert data.get("tier") == "desktop", (
        f"the user help is in tier {data.get('tier')!r}; it must be 'desktop' so "
        "it reaches an installed machine without anyone opting in"
    )


def test_the_source_is_pinned_by_sha256():
    """One source entry, pinned by a full sha256 — never a moving reference."""
    sources = recipe().get("source") or []
    assert len(sources) == 1, f"expected exactly one source entry, found {len(sources)}"
    entry = sources[0]
    digest = str(entry.get("sha256", ""))
    assert SHA256_RE.match(digest), (
        f"the source sha256 is {digest!r}; a pinned upstream tarball carries a "
        "full 64-character lowercase hex digest"
    )
    url = str(entry.get("url", ""))
    assert "${version}" in url or str(recipe().get("version")) in url, (
        f"the source url {url!r} does not name the packaged version, so the "
        "digest and the tarball can drift apart silently"
    )


def test_the_installed_help_directory_is_verified_after_install():
    """verify_paths names the directory the viewer actually reads.

    A build that configured, compiled and installed nothing under
    /usr/share/help would still be recorded as a good package without this,
    and the failure would first be visible to a person pressing F1.
    """
    verify = recipe().get("verify_paths") or []
    assert HELP_DIR in verify, (
        f"{HELP_DIR} is not in verify_paths, so a build that installed no pages "
        "would still be recorded as a good package"
    )


def test_the_mallard_toolchain_is_declared_as_a_build_dependency():
    """The tools that turn the Mallard sources into installed pages are declared."""
    deps = recipe().get("dependencies") or {}
    build = set(deps.get("build") or [])
    for tool in DOC_BUILD_TOOLS:
        assert tool in build, (
            f"{tool!r} is not a build dependency, although the sources are "
            "Mallard XML and do not become installed pages without it; the "
            "recipe would build only where the tool happens to be present"
        )


def test_the_guide_describes_the_shell_this_tree_ships():
    """The documentation's major version is the shell's major version.

    A guide a major version away from the running shell names menus and
    settings panels that do not exist in it.
    """
    docs_version = recipe().get("version")
    shell_version = read_recipe(SHELL_PACKAGE_YML).get("version")
    assert major(docs_version) == major(shell_version), (
        f"the user guide is {docs_version} and the shell is {shell_version}; a "
        "guide from a different major release describes a desktop this tree "
        "does not ship"
    )


def test_the_viewer_that_displays_these_pages_is_in_the_tree():
    """Pages and viewer ship together, or the pages are unreachable."""
    viewer = read_recipe(VIEWER_PACKAGE_YML)
    assert viewer.get("name") == "yelp"
    assert viewer.get("tier") == "desktop", (
        f"the help viewer is in tier {viewer.get('tier')!r}; the user help "
        "pages cannot be opened on an installed machine without it"
    )


def test_control_the_same_predicates_pass_for_a_package_already_shipping():
    """The positive control: these checks can pass, so a failure above is real.

    Applied to the document viewer, which this tree shipped before this file
    existed: a recipe in the desktop tier, a single sha256-pinned source, and
    verify_paths that name what it installs. A predicate that could never pass
    would fail here too.
    """
    data = read_recipe(CONTROL_PACKAGE_YML)
    assert data.get("tier") == "desktop"
    sources = data.get("source") or []
    assert len(sources) == 1
    assert SHA256_RE.match(str(sources[0].get("sha256", "")))
    assert CONTROL_VERIFY_PATH in (data.get("verify_paths") or [])
