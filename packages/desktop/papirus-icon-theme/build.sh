#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# papirus-icon-theme 20250501 — Papirus icon theme (3 variants: Papirus, Papirus-Dark, Papirus-Light)
#
# Upstream: github.com/PapirusDevelopmentTeam/papirus-icon-theme
# License: GPL-3.0-or-later
#
# Provides three icon-theme directories at /usr/share/icons/:
#   * Papirus        (default/light, follows system color scheme via symlinks)
#   * Papirus-Dark   (forces dark)
#   * Papirus-Light  (forces light)
#
# Papirus-Dark is a curated alternate (the system default is the
# first-party InterGenOS icon theme since 2026-07-23), referenced by 4 of
# the welcomer THEME_COMBOS (Orchis Dark / Catppuccin Mocha / Dracula /
# Nordic). Papirus (no suffix) is used by Orchis Light.
#
# Build profile: Make-based with DESTDIR + PREFIX honored. The upstream
# Makefile installs icon dirs by copy-recursive; gtk-update-icon-cache is
# auto-skipped when DESTDIR is set (per the Makefile's $(if $(DESTDIR),,))
# clause — clean and packaging-friendly.

configure() {
    set -e
    :  # No configure step — Makefile drives everything.
}

build() {
    set -e
    :  # No build step — icons are SVGs, no compile.
}

do_install() {
    set -e
    make install DESTDIR="${DESTDIR}" PREFIX=/usr
}

post_install() {
    set -e
    gtk-update-icon-cache -q /usr/share/icons/Papirus       2>/dev/null || true
    gtk-update-icon-cache -q /usr/share/icons/Papirus-Dark  2>/dev/null || true
    gtk-update-icon-cache -q /usr/share/icons/Papirus-Light 2>/dev/null || true
}
