#!/bin/bash
# adw-gtk3-theme 6.5 — libadwaita-styled GTK3 theme
#
# Upstream: github.com/lassekongo83/adw-gtk3 v6.5
# License: LGPL-2.1-or-later
#
# Provides two GTK3 theme directories at /usr/share/themes/:
#   * adw-gtk3       <-- referenced by Orchis Light welcomer combo
#   * adw-gtk3-dark  <-- referenced by Orchis Dark welcomer combo
#
# Standard meson + ninja build. Themes are compiled from SCSS via sassc
# during meson build, then installed under /usr/share/themes/.

configure() {
    set -e
    meson setup build --prefix=/usr --libdir=/usr/lib --buildtype=release
}

build() {
    set -e
    meson compile -C build
}

do_install() {
    set -e
    DESTDIR="${DESTDIR}" meson install -C build
}

post_install() {
    set -e
    :  # No cache needed for GTK themes.
}
