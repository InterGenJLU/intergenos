#!/bin/bash
# gcr4 4.3.0 — GNOME crypto and certificate library
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -e "/install_dir/s@,\$@ / 'gcr-${PKG_VERSION}'&@" -i ../docs/*/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dgtk_doc=false \
          -Dssh_agent=true
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
