#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# unifont 17.0.03 — install unifont.pcf so GRUB's build-time grub-mkfont can
# compile unicode.pf2 (BLFS 13.0 GRUB font procedure). Source is a single
# gzipped PCF (unifont-<ver>.pcf.gz); extract_source decompresses it to
# unifont-<ver>.pcf in the build dir (see scripts/pkg-functions.sh *.pcf.gz case).
# Built in Chapter 8 BEFORE grub, alongside freetype-grub.

configure() { set -e; :; }
build()     { set -e; :; }

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/fonts/unifont"
    # The decompressed PCF is the only file in the build dir.
    install -m644 unifont-*.pcf "${DESTDIR}/usr/share/fonts/unifont/unifont.pcf"
}
