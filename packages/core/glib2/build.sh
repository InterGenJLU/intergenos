#!/bin/bash
# glib2 2.86.4 — Low-level core library (with GObject Introspection)
# BLFS 13.0 — three-pass build: glib → g-i → glib with introspection
#
# DESTDIR-compatible: uses sysroot flags so GI can find glib in staging

configure() {
    # Apply required patch — fixes memory corruption exposed by glibc-2.43
    patch -Np1 -i "${IGOS_SOURCES}/glib-2.86.4-upstream_fixes-1.patch"

    mkdir build
    cd    build

    meson setup ..                  \
          --prefix=/usr             \
          --buildtype=release       \
          -D introspection=disabled \
          -D glib_debug=disabled    \
          -D man-pages=disabled     \
          -D sysprof=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build

    # Pass 1: install glib without introspection to DESTDIR
    DESTDIR="$DESTDIR" ninja install

    # Pass 2: build and install GObject Introspection
    # GI needs to find glib's headers and libs in the staging directory
    tar xf "${IGOS_SOURCES}/gobject-introspection-1.86.0.tar.xz"

    meson setup gobject-introspection-1.86.0 gi-build  \
                --prefix=/usr                           \
                --buildtype=release                     \
                -D gi_cross_pkgconfig_sysroot_path="${DESTDIR}"
    ninja -C gi-build
    DESTDIR="$DESTDIR" ninja -C gi-build install

    # Pass 3: fresh meson setup with introspection enabled
    # g-ir-scanner is in DESTDIR — put it on PATH BEFORE meson setup
    # so meson finds it during program search (meson configure won't re-scan)
    export PATH="${DESTDIR}/usr/bin:${PATH}"
    export PKG_CONFIG_PATH="${DESTDIR}/usr/lib/pkgconfig:${PKG_CONFIG_PATH}"
    export LD_LIBRARY_PATH="${DESTDIR}/usr/lib:${LD_LIBRARY_PATH}"

    cd ..
    mkdir build-pass3
    cd    build-pass3

    meson setup ..                  \
          --prefix=/usr             \
          --buildtype=release       \
          -D introspection=enabled  \
          -D glib_debug=disabled    \
          -D man-pages=disabled     \
          -D sysprof=disabled
    ninja
    DESTDIR="$DESTDIR" ninja install
}
