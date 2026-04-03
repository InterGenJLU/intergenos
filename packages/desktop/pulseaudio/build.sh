#!/bin/bash
# pulseaudio 17.0 — PulseAudio sound server
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Ddatabase=gdbm \
          -Dbluez5=disabled \
          -Ddoxygen=false \
          -Dtests=false \
          -Dudevrulesdir=/usr/lib/udev/rules.d
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Remove system-wide daemon config — PulseAudio should run per-user
    rm -fv "${DESTDIR}/usr/share/dbus-1/system.d/pulseaudio-system.conf"
}
