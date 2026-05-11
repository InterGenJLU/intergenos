#!/bin/bash
# tinysparql 3.10.1 — RDF graph database and SPARQL query engine
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -e "s/'generate'/\&, '--no-namespace-dir'/" -e "/--output-dir/s/@OUTPUT@/&\/tinysparql-${PKG_VERSION}/" -i docs/reference/meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Ddocs=false \
          -Dman=false \
          -Dtests=false
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
