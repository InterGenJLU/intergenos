#!/bin/bash
# whitesur-gtk-theme 2025-07-24 — macOS Big Sur-inspired GTK theme
#
# Upstream: github.com/vinceliuice/WhiteSur-gtk-theme
# License: GPL-3.0-or-later
#
# Installs GTK theme directories at /usr/share/themes/:
#   * WhiteSur, WhiteSur-Dark, WhiteSur-Light (and any extra color variants
#     produced by the install.sh default invocation)
#
# WhiteSur-Dark is referenced by the WhiteSur welcomer combo.
#
# Build profile: shell installer install.sh -d <dest>. The installer
# auto-detects sassc to compile SCSS → CSS. Defer gtk-update-icon-cache
# style calls to post_install convention even though GTK themes have
# no cache.

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
    install -dm755 "${DESTDIR}/usr/share/themes"
    bash install.sh -d "${DESTDIR}/usr/share/themes"
}

post_install() {
    set -e
    :
}
