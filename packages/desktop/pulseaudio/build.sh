#!/bin/bash
# pulseaudio 17.0 — PulseAudio sound server
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    # bluez5=disabled: PipeWire handles Bluetooth audio in the InterGenOS
    # stack. PipeWire's package.yml lists `bluez` as a runtime dep and
    # passes -Dbluez5-backend-native-mm=enabled. This disable is an
    # architectural choice (avoid duplicate BT-audio backends), not a
    # missing-dep bypass — bluez IS in tree.
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
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # BLFS: remove system-wide daemon D-Bus config — PulseAudio should run
    # per-user, not as a system daemon. Removed in DESTDIR so it never enters
    # the package manifest (prevents spurious rebuilds on skip-built checks).
    rm -fv "${DESTDIR}/usr/share/dbus-1/system.d/pulseaudio-system.conf"
}

post_install() {
    set -e
    udevadm control --reload 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
}
