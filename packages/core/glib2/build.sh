#!/bin/bash
# glib2 2.86.4 — Low-level core library (with GObject Introspection)
# BLFS 13.0 — two-pass build: glib -> g-i -> glib with introspection
# direct_install: true — installs to / (tracked via filesystem diff)

configure() {
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

    # Pass 1: install glib without introspection
    ninja install

    # Pass 2: build and install GObject Introspection
    tar xf "${IGOS_SOURCES}/gobject-introspection-1.86.0.tar.xz"

    meson setup gobject-introspection-1.86.0 gi-build \
                --prefix=/usr --buildtype=release
    ninja -C gi-build
    ninja -C gi-build install

    # Pass 3: rebuild glib with introspection enabled
    # (g-ir-scanner now installed to / from Pass 2)
    meson configure -D introspection=enabled
    ninja
    ninja install
}
