#!/bin/bash
# graphite-gtk-theme 2025-07-06 — minimal flat-design GTK theme
#
# Upstream: github.com/vinceliuice/Graphite-gtk-theme
# License: GPL-3.0-or-later
#
# Installs GTK theme directories at /usr/share/themes/:
#   * Graphite, Graphite-Dark, Graphite-Light (+ HDPI variants)
#
# Graphite-Dark is referenced by the Graphite welcomer combo (as both
# the gtk_theme AND the shell_theme).
#
# Build profile: shell installer install.sh -d <dest>. Compiles SCSS
# via sassc during install.

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
