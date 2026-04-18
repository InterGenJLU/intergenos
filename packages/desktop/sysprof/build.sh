#!/bin/bash
# sysprof 46.0 — System-wide profiler for Linux
# Builds capture library only (full profiler UI disabled)
# Required by: libspelling

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dsysprofd=none     \
          -Dtools=false       \
          -Dtests=false       \
          -Dexamples=false    \
          -Dhelp=false        \
          -Dgtk=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
