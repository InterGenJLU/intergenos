#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# wine-mono 11.2.0 — Mono .NET MSI addon (GE arc, D1 ADD)
# Pure data package: the MSI ships verbatim to wine's default addon
# search dir (pin provenance in package.yml).

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
    install -dm755 "${DESTDIR}/usr/share/wine/mono"
    install -m644 "wine-mono-${PKG_VERSION}-x86.msi" \
        "${DESTDIR}/usr/share/wine/mono/"
}
