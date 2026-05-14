#!/bin/bash
# orchis-theme 2025-04-25 — Material-Design-inspired GTK + gnome-shell theme
#
# Upstream: github.com/vinceliuice/Orchis-theme
# License: GPL-3.0-or-later
#
# Installs GTK + gnome-shell theme directories at /usr/share/themes/:
#   * Orchis, Orchis-Dark, Orchis-Light (+ HDPI + xHDPI + compact variants)
#
# Orchis is heavily-referenced by the welcomer:
#   * Orchis Dark combo  — shell_theme=Orchis-Dark
#   * Catppuccin Mocha   — shell_theme=Orchis-Dark
#   * Dracula            — shell_theme=Orchis-Dark
#   * Orchis Light combo — shell_theme=Orchis-Light
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
