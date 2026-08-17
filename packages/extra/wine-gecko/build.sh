#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# wine-gecko 2.47.4 — Gecko MSI addons, both PE widths (GE arc, D1 ADD)
# Pure data package: the MSIs ship verbatim to wine's default addon
# search dir (pin provenance + the silent-fetch rationale in package.yml).

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/wine/gecko"
    # source[0] (x86) was raw-copied into the src dir by the builder's
    # .msi branch; source[1] (x86_64) is a secondary source, handled
    # explicitly per Rule 5 from the staged sources dir.
    install -m644 "wine-gecko-${PKG_VERSION}-x86.msi" \
        "${DESTDIR}/usr/share/wine/gecko/"
    install -m644 "${IGOS_SOURCES}/wine-gecko-${PKG_VERSION}-x86_64.msi" \
        "${DESTDIR}/usr/share/wine/gecko/"
}
