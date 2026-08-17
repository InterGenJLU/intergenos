#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# polkit 127 — PolicyKit authorization toolkit
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..                  \
          --prefix=/usr             \
          --libdir=/usr/lib         \
          --libexecdir=/usr/libexec \
          --buildtype=release       \
          -Dtests=true              \
          -Dman=true                \
          -Dsession_tracking=logind \
          -Dos_type=lfs
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Set setuid bits — pkexec and polkit-agent-helper-1 need setuid for
    # privilege escalation. Must be set here because tar-based deployment
    # strips setuid bits.
    chmod 4755 "${DESTDIR}/usr/bin/pkexec"
    # polkit-agent-helper-1 uses 4711 (execute-only, not readable).
    # Path is hardcoded by polkit's meson.build: pk_libprivdir = 'lib' / pk_api_name
    # → /usr/lib/polkit-1/, regardless of --libexecdir.
    chmod 4711 "${DESTDIR}/usr/lib/polkit-1/polkit-agent-helper-1"
}
