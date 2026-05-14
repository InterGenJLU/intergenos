#!/bin/bash
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
    done
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
