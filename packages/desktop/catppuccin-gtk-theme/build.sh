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
    local theme_dir="catppuccin-mocha-blue-standard+default"
    # Tarball top-level dir is "${theme_dir}/". The builder extracts with
    # tar --strip-components=1 (builder.py:318), which strips that single
    # top-level component — so when we land in cwd, the dir itself is gone
    # and its contents (cinnamon/, gnome-shell/, gtk-3.0/, gtk-4.0/, etc.)
    # sit at cwd-root. Verify the structure looks right via one of the
    # expected sub-dirs, then re-create the canonical wrapper dir under
    # /usr/share/themes/ and copy the extracted contents into it. gschema +
    # welcomer reference the wrapper-dir name verbatim (literal `+` char).
    if [ ! -d "gtk-3.0" ] || [ ! -d "gtk-4.0" ]; then
        echo "catppuccin-gtk-theme: expected gtk-3.0/ + gtk-4.0/ at extract root; tarball layout changed" >&2
        exit 1
    fi
    install -dm755 "${DESTDIR}/usr/share/themes/${theme_dir}"
    cp -a ./. "${DESTDIR}/usr/share/themes/${theme_dir}/"
}

post_install() {
    set -e
    :
}
