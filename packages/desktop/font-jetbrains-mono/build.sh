#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# packages/desktop/font-jetbrains-mono/build.sh
#
# JetBrains Mono 2.304 — modern monospace variable font with programming
# ligatures. Curated subset: the two variable-weight .ttf files (Regular +
# Italic axes) + OFL + AUTHORS. Skips the 16 static-weight static .ttf files,
# the 16 "NL" (no-ligatures) variants, and the woff2 web fonts.
#
# Used as the InterGenOS monospace font default by requirement
# 2026-05-22. Pairs with Inter for the system/document/titlebar fonts.

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

    install -dm755 "${DESTDIR}/usr/share/fonts/jetbrains-mono"
    tar -xzf "${assets}/font-jetbrains-mono-2.304.tar.gz" \
        -C "${DESTDIR}/usr/share/fonts/jetbrains-mono/"

    # Defensive assertion: license + authors must be present (the variable
    # .ttf filenames contain literal '[wght]' which is awkward to verify
    # via shell; license/authors files are the safer sentinels).
    for f in OFL.txt AUTHORS.txt; do
        if [ ! -f "${DESTDIR}/usr/share/fonts/jetbrains-mono/${f}" ]; then
            echo "FATAL: ${f} missing in DESTDIR after extraction" >&2
            exit 1
        fi
    done
}

post_install() {
    set -e
    # Refresh the system font cache so fontconfig + terminals pick up the family.
    fc-cache -fv /usr/share/fonts/jetbrains-mono 2>/dev/null || true
}
