#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# unzip 6.0 — Info-ZIP .zip extractor.
#
# Info-ZIP UnZip 6.0 is the final upstream release (2009, unmaintained). The
# security-relevant patch set is Debian's maintained 6.0-29 series, staged as a
# pinned secondary source (unzip-6.0-29.debian.tar.xz) and applied here in the
# upstream series order, EXCLUDING 02-this-is-debian-unzip (Debian branding).
# The series carries the full known CVE set through 2022 (see package.yml).
#
# Built with `gcc -std=gnu89` (the same as the zip sibling): Info-ZIP 6.0
# predates C99 and uses implicit-int / old-style constructs that GCC 14+/15
# reject by default, so gnu89 compiles them as written and no separate
# gcc-compat patch is needed. The plain `generic` target does NOT define
# UNICODE_SUPPORT, so the wide->local conversion path guarded by the
# 2022-0529/0530 fix is not even compiled — the fix is present in source and
# the surface is absent, while the always-compiled paths (zip-bomb 2019-13232,
# 2021-4217) are patched.

configure() {
    set -e
    # Apply Debian's maintained 6.0-29 patch series in upstream order. The
    # secondary source extracts a debian/ dir alongside the unzip sources
    # (Rule 5 — secondary source extracted explicitly). Skip 02 (branding).
    tar -xf "$IGOS_SOURCES/unzip-6.0-29.debian.tar.xz"
    local p
    while read -r p; do
        [ -z "$p" ] && continue
        case "$p" in 02-*) continue ;; esac
        patch -Np1 -i "debian/patches/$p"
    done < debian/patches/series
}

build() {
    set -e
    # generic target + gnu89 (see header). No configure step of its own; the
    # target runs Info-ZIP's flags probe then builds the unix binary.
    make -f unix/Makefile generic \
        CC="gcc -std=gnu89"
}

do_install() {
    set -e
    make prefix="$DESTDIR/usr"                   \
         MANDIR="$DESTDIR/usr/share/man/man1"    \
         -f unix/Makefile install
}
