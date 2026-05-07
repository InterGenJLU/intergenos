#!/bin/bash
# gnome-system-monitor 49.1 — GNOME system monitor
# BLFS 13.0

configure() {
    # pipefail: find | xargs pipe — without pipefail, find failure
    # would be masked by xargs succeeding on empty input
    set -e -o pipefail
    # BLFS required fix — remove catch2 test dependency
    find . -name meson.build | xargs sed -i -e '/catch2/d'
    sed -i '145,155d' src/meson.build

    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release
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
}
