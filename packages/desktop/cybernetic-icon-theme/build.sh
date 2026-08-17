#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# packages/desktop/cybernetic-icon-theme/build.sh
#
# Cybernetic Blue icon theme 2.0 — featured alternate icon theme
# (decided 2026-05-22: included alternate with attribution; since
# 2026-07-23 the installed default is the first-party InterGenOS icon
# theme, with Papirus-Dark as a further alternate).
#
# Upstream: https://github.com/SethStormR/Cybernetic (Author: SethStormR)
# License: GPL-3.0-or-later (per upstream LICENSE; CREDITS +
# THIRD-PARTY-NOTICES.md carry the attribution).
#
# Source bundle: assets/Cybernetic.tar.gz (asset-in-package). Pulled from
# the working IGOS laptop's /usr/share/icons/Cybernetic - Blue (per
# operator 2026-05-22: "Cybernetic is already ON the IGOS laptop — I used
# it there extensively for quite some time"). Integrity verifiable via
# `sha256sum` (git tracks the binary at this path).
#
# Tarball ships single top-level directory "Cybernetic - Blue/" (note the
# literal spaces + hyphen). Install path mirrors that: /usr/share/icons/
# Cybernetic - Blue/.
#
# Closes:
#   - Theming-arc dispatch Item A (Cybernetic icon theme inclusion)
#   - Audit row J-008 (icon-theme contradiction; resolves via Papirus-default
#     + Cybernetic-alternate)
#   - Migrates Cybernetic install away from install-theming.sh (dispatch
#     Item J — install-theming.sh retirement coupling)

configure() { :; }
build() { :; }

do_install() {
    set -e
    local assets="${ASSETS}"
    if [ -z "$assets" ] || [ ! -d "$assets" ]; then
        # build.sh is sourced; use ${BASH_SOURCE[0]} (not $0 which is the
        # calling chroot-build script).
        assets="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/assets"
    fi

    install -dm755 "${DESTDIR}/usr/share/icons"

    # Extract the bundled tarball directly into /usr/share/icons. The
    # tarball contains exactly one top-level directory: "Cybernetic - Blue/"
    # with full theme content; tar preserves SVG symlinks + ownership bits.
    tar -xzf "${assets}/Cybernetic.tar.gz" -C "${DESTDIR}/usr/share/icons/"

    # Normalize modes: tar preserves whatever the capture source carried,
    # and this bundle's capture machine had the theme root 0770 -- a GTK
    # icon theme no unprivileged session could traverse shipped on two
    # candidates before anyone noticed. Data trees are 755/644, always.
    find "${DESTDIR}/usr/share/icons" -type d -exec chmod 755 {} +
    find "${DESTDIR}/usr/share/icons" -type f -exec chmod 644 {} +

    # Defensive assertion: confirm the canonical theme directory exists with
    # an index.theme inside. Halt the build if the tarball structure drifted.
    if [ ! -f "${DESTDIR}/usr/share/icons/Cybernetic - Blue/index.theme" ]; then
        echo "FATAL: Cybernetic - Blue/index.theme missing in DESTDIR" >&2
        echo "Tarball structure may have changed upstream; verify the bundle" >&2
        exit 1
    fi
}

post_install() {
    set -e
    # Generate the icon-cache for the theme. Without this, GNOME falls back
    # to scanning the entire theme dir on every icon lookup -- noticeable
    # startup lag on slower disks.
    gtk-update-icon-cache -q "/usr/share/icons/Cybernetic - Blue" 2>/dev/null || true
}
