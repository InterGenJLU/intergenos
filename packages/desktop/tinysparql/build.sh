#!/bin/bash
# tinysparql 3.10.1 — RDF graph database and SPARQL query engine
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -e "s/'generate'/\&, '--no-namespace-dir'/" -e "/--output-dir/s/@OUTPUT@/&\/tinysparql-${PKG_VERSION}/" -i docs/reference/meson.build
    mkdir build
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
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
