#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# intergenos-extensions-layout 1.0 — Layout-category GNOME Shell extensions super-package
#
# Welcomer category: Layout (4 extensions; none are enabled by default
# in our gschema override — these are user-selectable opt-ins for
# users who want a different shell layout than the GNOME default).
#
# Bundled extensions:
#   * dash-to-dock@micxgx.gmail.com         — GPL-2.0; persistent dock on any screen edge
#   * dash-to-panel@jderose9.github.com     — GPL-2.0; Windows/KDE-style taskbar
#   * arcmenu@arcmenu.com                   — GPL-2.0; full app menu with search + layouts
#   * show-desktop-button@amivaleo          — GPL-3.0; one-click minimize-all-windows

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

do_install() {
    set -e

    # InterGenOS downstream ArcMenu patch — applied to the bundled
    # arcmenu@arcmenu.com source tree BEFORE it is copied into place. Adds a
    # custom "InterGenOS" system category (collects every app whose .desktop
    # Categories carries the X-InterGenOS token), a greyscale boxed category
    # icon (intergenos-category-symbolic, shipped by intergen-mark), and a
    # hover tooltip on the panel start button. ArcMenu has no custom-category
    # support upstream, so this is a real carried downstream patch.
    # Mirrors the gtk4 pattern:
    # IGOS_PACKAGE_DIR points at the recipe dir; fall back to the workspace path
    # for surgical-rebuild invocations that don't propagate it. cwd here is the
    # extracted source root (the extension UUID dirs sit at top level), so the
    # a/arcmenu@arcmenu.com/... patch paths strip cleanly with -p1.
    local patches_dir="${IGOS_PACKAGE_DIR:-/mnt/intergenos/packages/desktop/intergenos-extensions-layout}/patches"
    if [ -d "$patches_dir" ]; then
        for p in "$patches_dir"/*.patch; do
            [ -f "$p" ] || continue
            echo "Applying patch: $(basename "$p")"
            patch -p1 -i "$p"
        done
    fi

    install -dm755 "${DESTDIR}/usr/share/gnome-shell/extensions"
    for uuid in \
        dash-to-dock@micxgx.gmail.com \
        dash-to-panel@jderose9.github.com \
        arcmenu@arcmenu.com \
        show-desktop-button@amivaleo ; do
        if [ ! -d "${uuid}" ] ; then
            echo "intergenos-extensions-layout: bundle missing expected extension '${uuid}'" >&2
            exit 1
        fi
        cp -a "${uuid}" "${DESTDIR}/usr/share/gnome-shell/extensions/"
        # cp -a preserves the upstream tarball's perms; several bundles
        # ship metadata.json mode 600. gnome-shell runs as the user
        # (uid 1000) and any extension file that isn't world-readable
        # fails to load with "Permission denied" on metadata.json.
        # a+rX adds world-read everywhere + world-execute only on dirs
        # and already-executable files.
        chmod -R a+rX "${DESTDIR}/usr/share/gnome-shell/extensions/${uuid}"
        # GBC001.3-rebuild: compile this extension's OWN schemas/ dir so
        # gnome-shell's getSettings() finds <uuid>/schemas/gschemas.compiled
        # at runtime. Upstream bundles ship schemas/*.gschema.xml but NOT the
        # compiled binary; without it the extension throws GLib.FileError
        # ("Failed to open .../schemas/gschemas.compiled") and silently fails
        # to load. The system-dir compile in post_install does NOT cover
        # per-extension schema dirs. This package (layout) was missed when the
        # fix landed in the other three extension super-packages — arcmenu in
        # particular ships only the .gschema.xml, so it was dead on every
        # install (State: ERROR) until now (caught on a development machine, 2026-06-10).
        _schemadir="${DESTDIR}/usr/share/gnome-shell/extensions/${uuid}/schemas"
        if ls "${_schemadir}/"*.gschema.xml >/dev/null 2>&1; then
            glib-compile-schemas "${_schemadir}"
        fi
    done
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
