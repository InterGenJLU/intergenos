#!/bin/bash
# babl 0.1.122 — Dynamic pixel format translation library
# BLFS 13.0

configure() {
    mkdir bld
    cd    bld

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release
}

build() {
    cd bld
    ninja
}

do_install() {
    cd bld
    DESTDIR="$DESTDIR" ninja install

    install -v -m755 -d                         "$DESTDIR/usr/share/gtk-doc/html/babl/graphics"
    install -v -m644 docs/*.css docs/*.html     "$DESTDIR/usr/share/gtk-doc/html/babl"          2>/dev/null || true
    install -v -m644 docs/graphics/*.html \
                     docs/graphics/*.svg        "$DESTDIR/usr/share/gtk-doc/html/babl/graphics" 2>/dev/null || true
}
