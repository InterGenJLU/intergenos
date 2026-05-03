#!/bin/bash
# sysprof 46.0 — System-wide profiler for Linux
# Builds capture library only (full profiler UI disabled)
# Required by: libspelling

configure() {
    mkdir build
    cd    build

    # -Dlibsysprof=false: skip src/libsysprof/ which references
    # libeggbitset_static_dep unconditionally at meson.build:193 even
    # though contrib/eggbitset/ is only entered when need_glib is true
    # (sysprof 46.0 upstream bug — asymmetric gating). libspelling, our
    # only consumer, needs sysprof-capture-4 only and does the same
    # libsysprof=false when building sysprof as a meson subproject.
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dsysprofd=none     \
          -Dlibsysprof=false  \
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
