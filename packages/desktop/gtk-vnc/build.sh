#!/bin/bash
# gtk-vnc 1.5.0 — VNC viewer widget for GTK
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    # -Dpulseaudio=enabled: explicitly require pulseaudio support so
    # the gvncpulse-1.0 sub-library is built. gnome-connections needs
    # gvncpulse-1.0 for VNC audio; without an explicit setting (option
    # defaults to 'auto'), gtk-vnc may build without it if libpulse
    # wasn't visible at configure time.
    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release   \
          -Dpulseaudio=enabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
