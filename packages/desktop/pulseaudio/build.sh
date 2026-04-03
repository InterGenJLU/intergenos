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

post_install() {
    # Remove system-wide daemon config — PulseAudio should run per-user
    rm -fv /usr/share/dbus-1/system.d/pulseaudio-system.conf
}
