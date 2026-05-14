#!/bin/bash
# fluent-gtk-theme 2025-04-17 — Microsoft Fluent Design-inspired GTK theme
#
# Upstream: github.com/vinceliuice/Fluent-gtk-theme
# License: GPL-3.0-or-later
#
# Installs GTK + gnome-shell theme directories at /usr/share/themes/:
#   * Fluent, Fluent-Dark, Fluent-Light (+ compact variants)
#
# Fluent-Dark is referenced by the Fluent welcomer combo (as both
# gtk_theme AND shell_theme).
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
