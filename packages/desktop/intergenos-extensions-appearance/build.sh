#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# intergenos-extensions-appearance 1.0 — Appearance-category GNOME Shell extensions super-package
#
# Welcomer category: Appearance (5 extensions, one of which is enabled by
# default per the gschema enabled-extensions list).
#
# Bundled extensions (with welcomer-grouping rationale + license):
#   * blur-my-shell@aunetx           — GPL-3.0; default-enabled; blur effects on panel/overview/lockscreen
#   * burn-my-windows@schneegans...  — GPL-3.0; default-enabled; stylish window open/close animations
#   * rounded-window-corners@fxgn    — GPL-3.0; rounded corners on all windows (fxgn fork active maint)
#   * desktop-cube@schneegans...     — GPL-3.0; 3D cube workspace switching
#   * nightthemeswitcher@romain...   — GPL-3.0; auto light/dark by time of day
#
# Each extension is a self-contained directory under
# /usr/share/gnome-shell/extensions/<uuid>/ containing extension.js
# (or compiled TypeScript output), metadata.json, optional schemas/,
# and an optional locale/ tree. The source tarball preserves the
# upstream layout; do_install copies each UUID dir verbatim.

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
    install -dm755 "${DESTDIR}/usr/share/gnome-shell/extensions"
    for uuid in \
        blur-my-shell@aunetx \
        burn-my-windows@schneegans.github.com \
        rounded-window-corners@fxgn \
        desktop-cube@schneegans.github.com \
        nightthemeswitcher@romainvigier.fr ; do
        if [ ! -d "${uuid}" ] ; then
            echo "intergenos-extensions-appearance: bundle missing expected extension '${uuid}'" >&2
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
        # per-extension schema dirs — blur-my-shell / CoverflowAltTab /
        # bluetooth-quick-connect / burn-my-windows were all dead on the
        # GBC001.3 boot for exactly this reason.
        _schemadir="${DESTDIR}/usr/share/gnome-shell/extensions/${uuid}/schemas"
        if ls "${_schemadir}/"*.gschema.xml >/dev/null 2>&1; then
            glib-compile-schemas "${_schemadir}"
        fi
    done
}

post_install() {
    set -e
    # Each extension's schemas/ subdir, if present, holds compiled
    # GSettings schemas that need installation to the system schema
    # path. The simplest universal approach is to compile the global
    # /usr/share/glib-2.0/schemas/ tree, which will pick up any
    # extension-shipped overrides that were placed there.
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
