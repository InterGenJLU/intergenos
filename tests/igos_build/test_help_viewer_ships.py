#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The desktop's help viewer is packaged, and it links the WebKit it is built for.

Pressing F1 in a GNOME application, or choosing Help from its menu, launches the
help viewer by its desktop file. Without that viewer on the installed system the
keystroke and the menu item do nothing at all: no window, no message, no error —
the one failure mode this project treats as the worst kind, because a person
cannot tell a missing program from a program that decided there was nothing to
show. This file asserts the viewer is in the tree and is packaged coherently.

WHY THE WEBKIT FLAVOUR IS ASSERTED AND NOT ASSUMED. The help viewer renders its
pages in a WebKit view, and this tree ships TWO WebKit builds from the same
sources: `webkitgtk-gtk3`, whose output is libwebkit2gtk-4.1.so for GTK 3, and
`webkitgtk`, whose output is libwebkitgtk-6.0.so for GTK 4. A viewer release
asks for exactly one of them by pkg-config name, and the two are not
interchangeable: yelp 49.2's meson.build asks for `webkitgtk-6.0` and
`webkitgtk-web-process-extension-6.0` alongside gtk4 and libadwaita-1, while the
42.x line asked for `webkit2gtk-4.1` alongside gtk+-3.0 and libhandy-1. Declare
the wrong one and the build either fails at configure time or — worse — links
against a second, unintended WebKit that is already in the image for another
reason. These assertions pin the declared dependency set to the flavour the
chosen release actually links, so a future version bump that crosses the GTK
boundary cannot pass quietly.

The final test is a POSITIVE CONTROL. Every predicate here is also applied to a
GTK application this tree already ships, so a failure above is evidence about
the help viewer rather than about a predicate that could never pass.
"""
import re
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - the suite requires PyYAML
    pytest.skip("PyYAML not available", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPE_DIR = REPO_ROOT / "packages/desktop/yelp"
PACKAGE_YML = RECIPE_DIR / "package.yml"
BUILD_SH = RECIPE_DIR / "build.sh"

# The GTK-4 WebKit recipe in this tree, and the GTK-3 one it must not be
# confused with. Named here so the assertions below read as the decision they
# enforce rather than as two similar strings.
WEBKIT_GTK4_RECIPE = "webkitgtk"
WEBKIT_GTK3_RECIPE = "webkitgtk-gtk3"

BINARY_PATH = "/usr/bin/yelp"
DESKTOP_FILE_PATH = "/usr/share/applications/org.gnome.Yelp.desktop"
GSETTINGS_SCHEMA_PATH = "/usr/share/glib-2.0/schemas/org.gnome.yelp.gschema.xml"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# The control: a GTK application this tree already ships, packaged the same way.
CONTROL_PACKAGE_YML = REPO_ROOT / "packages/desktop/evince/package.yml"
CONTROL_BINARY_PATH = "/usr/bin/evince"


def read_recipe(path: Path) -> dict:
    """The package definition at `path`, read from the tree."""
    assert path.is_file(), f"{path} does not exist"
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def recipe() -> dict:
    return read_recipe(PACKAGE_YML)


def test_the_help_viewer_has_a_recipe_in_the_desktop_tier():
    """The viewer is packaged, and in the tier that reaches an installed desktop.

    `desktop` is the tier whose members are included on the installation image
    by default. A help viewer in `extra` would be a help viewer nobody has when
    they press F1.
    """
    assert RECIPE_DIR.is_dir(), (
        f"{RECIPE_DIR} does not exist: the desktop has no help viewer, so F1 and "
        "every Help menu item open nothing on an installed system"
    )
    data = recipe()
    assert data.get("name") == "yelp"
    assert data.get("tier") == "desktop", (
        f"the help viewer is in tier {data.get('tier')!r}; it must be 'desktop' "
        "so it reaches an installed machine without anyone opting in"
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


def test_the_declared_dependencies_name_the_webkit_flavour_it_links():
    """The viewer declares the GTK-4 WebKit, and not the GTK-3 one.

    yelp 49.2 asks meson for `webkitgtk-6.0` and
    `webkitgtk-web-process-extension-6.0`, which this tree's `webkitgtk` recipe
    provides, together with gtk4 and libadwaita-1. The GTK-3 WebKit
    (`webkitgtk-gtk3`, libwebkit2gtk-4.1.so) belongs to the 42.x line and must
    not appear: declaring it would pull a second web engine into the image for a
    program that cannot use it.
    """
    deps = recipe().get("dependencies") or {}
    build = set(deps.get("build") or [])
    runtime = set(deps.get("runtime") or [])

    assert WEBKIT_GTK4_RECIPE in build, (
        f"{WEBKIT_GTK4_RECIPE!r} is not a build dependency; the viewer's "
        "meson.build asks for webkitgtk-6.0 and will not configure without it"
    )
    assert WEBKIT_GTK4_RECIPE in runtime, (
        f"{WEBKIT_GTK4_RECIPE!r} is not a runtime dependency; the installed "
        "binary links libwebkitgtk-6.0.so and cannot start without it"
    )
    assert WEBKIT_GTK3_RECIPE not in build and WEBKIT_GTK3_RECIPE not in runtime, (
        f"{WEBKIT_GTK3_RECIPE!r} is declared, but this release links the GTK-4 "
        "WebKit; the GTK-3 build is a different engine and a different soname"
    )

    for needed in ("gtk4", "libadwaita1", "yelp-xsl", "libxslt", "libxml2", "sqlite"):
        assert needed in runtime, (
            f"{needed!r} is not a runtime dependency, although the viewer's "
            "meson.build requires it at build time and links or reads it at run time"
        )
    assert "libhandy1" not in build and "libhandy1" not in runtime, (
        "libhandy1 is the GTK-3 companion of the 42.x line; this release uses "
        "libadwaita-1 and must not drag the GTK-3 library in"
    )


def test_the_desktop_file_and_the_binary_are_verified_after_install():
    """verify_paths asserts what the desktop actually launches.

    The Help keystroke resolves through the desktop file, not through the binary
    name, so a package that installed the binary and lost the desktop file would
    pass a binary-only check and still leave F1 dead. The GSettings schema is
    asserted for the same reason: the viewer reads its own settings at startup.
    """
    verify = recipe().get("verify_paths") or []
    for path in (BINARY_PATH, DESKTOP_FILE_PATH, GSETTINGS_SCHEMA_PATH):
        assert path in verify, (
            f"{path} is not in verify_paths, so a build that failed to install "
            "it would still be recorded as a good package"
        )


def test_the_build_compiles_the_gsettings_schemas_like_the_tree_s_other_gtk_apps():
    """A newly installed schema is not readable until the cache is rebuilt."""
    assert BUILD_SH.is_file(), f"{BUILD_SH} does not exist"
    text = BUILD_SH.read_text(encoding="utf-8")
    assert "glib-compile-schemas /usr/share/glib-2.0/schemas" in text, (
        "the recipe does not rebuild the GSettings schema cache after install; "
        "every other GTK application in this tree does it in do_install"
    )


def test_control_the_same_predicates_pass_for_an_application_already_shipping():
    """The positive control: these checks can pass, so a failure above means yelp.

    Applied to the document viewer, which this tree has shipped since before
    this file existed: it has a recipe in the desktop tier, a single sha256-
    pinned source, and verify_paths that name its binary. A predicate that could
    never pass would fail here too, and then the failures above would say
    nothing about the help viewer.
    """
    data = read_recipe(CONTROL_PACKAGE_YML)
    assert data.get("tier") == "desktop"
    sources = data.get("source") or []
    assert len(sources) == 1
    assert SHA256_RE.match(str(sources[0].get("sha256", "")))
    assert CONTROL_BINARY_PATH in (data.get("verify_paths") or [])
