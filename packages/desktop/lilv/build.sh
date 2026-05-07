#!/bin/bash
# lilv 0.26.4 — LV2 plugin host library (drobilla)
# Discovers, loads, and runs LV2 plugins on behalf of audio applications
# (Audacity, etc.). Sits on top of the lv2 spec headers and the drobilla
# RDF stack: zix (data structures), serd (Turtle parser), sord (in-memory
# RDF store), sratom (LV2 atom <-> RDF). Pkg-config file installed is
# `lilv-0.pc` (versioned per upstream's parallel-major-version convention).
# BLFS does not (yet) carry lilv — upstream is the source of truth.

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Ddocs=disabled     \
          -Dhtml=disabled     \
          -Dsinglehtml=disabled \
          -Dtests=disabled    \
          -Dtools=enabled     \
          -Dbindings_cpp=disabled \
          -Dbindings_py=disabled \
          -Ddynmanifest=disabled
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
