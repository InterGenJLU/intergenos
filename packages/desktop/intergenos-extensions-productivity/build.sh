#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# intergenos-extensions-productivity 1.0 — Productivity-category GNOME Shell extensions super-package
#
# Welcomer category: Productivity (6 extensions, one of which —
# CoverflowAltTab — is enabled by default per the gschema
# enabled-extensions list).
#
# Bundled extensions:
#   * CoverflowAltTab@palatis.blogspot.com  — GPL-2.0; default-enabled; 3D window switcher
#   * clipboard-indicator@tudmotu.com       — GPL-3.0; clipboard history with search
#   * tilingshell@ferrarodomenico.com       — GPL-3.0; Windows-style snap + custom layouts
#   * forge@jmmaranan.com                   — GPL-3.0; i3-style auto-tiling WM
#   * ddterm@amezin.github.com              — GPL-3.0; Quake-style drop-down terminal
#   * AlphabeticalAppGrid@stuarthayhurst    — GPL-3.0; sort app grid alphabetically

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
        CoverflowAltTab@palatis.blogspot.com \
        clipboard-indicator@tudmotu.com \
        tilingshell@ferrarodomenico.com \
        forge@jmmaranan.com \
        ddterm@amezin.github.com \
        AlphabeticalAppGrid@stuarthayhurst ; do
        if [ ! -d "${uuid}" ] ; then
            echo "intergenos-extensions-productivity: bundle missing expected extension '${uuid}'" >&2
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
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
