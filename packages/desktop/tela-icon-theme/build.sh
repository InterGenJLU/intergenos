#!/bin/bash
# tela-icon-theme 2025-02-10 — Tela icon theme (3 brightness variants of the standard color)
#
# Upstream: github.com/vinceliuice/Tela-icon-theme
# License: GPL-3.0-or-later
#
# Installs three icon-theme directories at /usr/share/icons/:
#   * Tela        (default)
#   * Tela-light
#   * Tela-dark   <-- referenced by the Graphite welcomer combo
#
# Tela ships 15 color variants × 3 brightness = 45 total possible variants.
# We install only the default "standard" color set (3 dirs) to keep the
# disk footprint reasonable. Other colors are an upstream feature we can
# expose via a separate package if user demand surfaces.
#
# Build profile: shell installer install.sh -d <dest>. The installer's
# default invocation selects the standard color + all 3 brightness
# variants. Same gtk-update-icon-cache-stripping pattern as the other
# vinceliuice icon-theme packages.

configure() {
    set -e
    sed -i 's|gtk-update-icon-cache|true|g' install.sh
}

build() {
    set -e
    :
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/icons"
    bash install.sh -d "${DESTDIR}/usr/share/icons"
}

post_install() {
    set -e
    for d in Tela Tela-light Tela-dark ; do
        gtk-update-icon-cache -q "/usr/share/icons/${d}" 2>/dev/null || true
    done
}
