#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# packages/desktop/font-inter/build.sh
#
# Inter 4.1 — clean modern geometric sans variable font. Curated subset:
# only the two variable-font .ttf files + LICENSE. Skips the upstream
# Inter.ttc (13MB TrueType Collection — superseded by the variable .ttf
# in modern fontconfig), the woff-hinted web variants, and the static-
# weight individual .ttf files.
#
# Used as the InterGenOS system + document + titlebar font default per
# requirement 2026-05-22. Matches the restrained-geometric voice
# of the InterGenOS wordmark.

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

    install -dm755 "${DESTDIR}/usr/share/fonts/inter"
    tar -xzf "${assets}/font-inter-4.1.tar.gz" -C "${DESTDIR}/usr/share/fonts/inter/"

    # Normalize modes: tar preserves the capture source's bits, and a
    # sibling bundle shipped its theme root 0770 that way. 755/644 always.
    find "${DESTDIR}/usr/share/fonts" -type d -exec chmod 755 {} +
    find "${DESTDIR}/usr/share/fonts" -type f -exec chmod 644 {} +

    # Defensive assertion: the variable .ttf files must be present.
    for f in InterVariable.ttf InterVariable-Italic.ttf LICENSE.txt; do
        if [ ! -f "${DESTDIR}/usr/share/fonts/inter/${f}" ]; then
            echo "FATAL: ${f} missing in DESTDIR after extraction" >&2
            exit 1
        fi
    done
}

post_install() {
    set -e
    # Refresh the system font cache so fontconfig + GTK pick up the new family.
    fc-cache -fv /usr/share/fonts/inter 2>/dev/null || true
}
