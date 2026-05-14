#!/bin/bash
# catppuccin-gtk-theme 1.0.3 — Catppuccin Mocha-Blue-Standard GTK theme
#
# Upstream: github.com/catppuccin/gtk v1.0.3
# License: MIT
#
# Installs one GTK theme directory at /usr/share/themes/:
#   * catppuccin-mocha-blue-standard+default  <-- exact welcomer reference
#
# Source provenance: Catppuccin GTK ships pre-built theme bundles as
# .zip release assets (one per flavor × accent combination). We ship
# the Mocha-Blue-Standard variant which is the exact GTK theme name
# the welcomer's Catppuccin Mocha combo references. Other flavors
# (Frappe, Latte, Macchiato) and other accent colors are available
# upstream — packageable as siblings if user demand surfaces.
#
# The upstream build.py + install.py scripts are NOT used here; they
# download release assets at runtime which is not viable in a
# hermetic chroot build. The .zip is referenced directly as the
# source.
#
# The directory name contains a literal `+` character (the theme is
# canonically named "<flavor>-<accent>-<size>+<style>"). This is
# preserved verbatim because gschema and the welcomer's THEME_COMBOS
# reference it that way.

configure() {
    set -e
    :  # No configure; .zip is the source.
}

build() {
    set -e
    :
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/themes"
    local theme_dir="catppuccin-mocha-blue-standard+default"
    if [ ! -d "${theme_dir}" ] ; then
        echo "catppuccin-gtk-theme: expected dir '${theme_dir}' missing in source tarball" >&2
        exit 1
    fi
    cp -a "${theme_dir}" "${DESTDIR}/usr/share/themes/"
}

post_install() {
    set -e
    :
}
