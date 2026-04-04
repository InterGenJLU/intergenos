#!/bin/bash
# pulseaudio 17.0 — PulseAudio sound server
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
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

    # BLFS: remove system-wide daemon D-Bus config — PulseAudio should run
    # per-user, not as a system daemon. Removed in DESTDIR so it never enters
    # the package manifest (prevents spurious rebuilds on skip-built checks).
    rm -fv "${DESTDIR}/usr/share/dbus-1/system.d/pulseaudio-system.conf"
}
