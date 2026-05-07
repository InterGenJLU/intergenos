#!/bin/bash
# lv2 1.18.10 — LV2 audio plugin specification (drobilla/lv2plug.in)
# Headers + bundles + a small set of example plugins. The headers are
# what host libraries (lilv) and audio applications (Audacity, etc.)
# build against. BLFS does not (yet) carry LV2 — upstream is the source
# of truth.

configure() {
    set -e
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Ddocs=disabled     \
          -Donline_docs=false \
          -Dplugins=enabled   \
          -Dtests=disabled
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
