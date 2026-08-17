#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# geoclue2 2.8.0 — D-Bus geolocation service
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk-doc=false \
          -D3g-source=false \
          -Dmodem-gps-source=false \
          -Dcdma-source=false \
          -Dnmea-source=false
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

    # The build compiles WITHOUT the 3g / modem-gps / cdma / network-nmea
    # backends (the -D*-source=false flags above), but upstream's default
    # geoclue.conf still ships those four sources with enable=true. At runtime
    # geoclue then logs "Source 'X' is enabled in configuration, but Geoclue is
    # compiled without it" for each, on every start (GBC003 G3-13). Make the
    # shipped config match the build: flip enable=true -> enable=false ONLY
    # within those four sections (DESTDIR so it reaches squashfs AND the package
    # archive). Other sources (e.g. network/wifi) are untouched.
    conf="$DESTDIR/etc/geoclue/geoclue.conf"
    if [ -f "$conf" ]; then
        awk '
          /^\[/ { sect = $0 }
          (sect ~ /^\[(3g|cdma|modem-gps|network-nmea)\]$/ && $0 == "enable=true") {
              print "enable=false"; next
          }
          { print }
        ' "$conf" > "$conf.igos.tmp" && mv "$conf.igos.tmp" "$conf"
    fi
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
