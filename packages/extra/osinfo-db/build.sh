#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# osinfo-db 20251212 — OS metadata database
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# Pure data package: the XML database libosinfo reads (OS identities,
# install-media detection rules, device defaults). Installed with
# osinfo-db-import from the pinned release archive — the upstream-
# supported install path (the archive is not a conventional
# ./configure source tree).

configure() {
    set -e
    : # No configure step — data archive, installed by osinfo-db-import.
}

build() {
    set -e
    : # No compile step — pure data.
}

do_install() {
    set -e
    install -d "$DESTDIR/usr/share/osinfo"
    osinfo-db-import \
        --dir "$DESTDIR/usr/share/osinfo" \
        "$IGOS_SOURCES/osinfo-db-$PKG_VERSION.tar.xz"
    test -d "$DESTDIR/usr/share/osinfo/os"   # fail loudly on an empty import
}
