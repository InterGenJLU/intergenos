#!/bin/bash
# librsvg 2.61.4 — SVG rendering library
# BLFS 13.0

configure() {
    set -e
    # Extract vendored crate dependencies (built offline on host)
    tar xf "${IGOS_SOURCES}/librsvg-${PKG_VERSION}-vendor.tar.xz" --strip-components=1

    # Fix API documentation install path (from BLFS)
    sed -e "/OUTDIR/s|,| / 'librsvg-${PKG_VERSION}', '--no-namespace-dir',|" \
        -e '/output/s|Rsvg-2.0|librsvg-'"${PKG_VERSION}"'|'                  \
        -i doc/meson.build

    mkdir -p build
    cd    build

    meson setup --prefix=/usr --buildtype=release \
          --libdir=/usr/lib   \
          -Dpixbuf-loader=enabled \
          ..
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    cd build
    meson test -v || true
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    set -e
    gdk-pixbuf-query-loaders --update-cache
}
