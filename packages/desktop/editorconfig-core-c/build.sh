#!/bin/bash
# editorconfig-core-c 0.12.9 — EditorConfig core C library
# Required by: gnome-text-editor

configure() {
    mkdir build
    cd    build

    cmake ..                              \
          -DCMAKE_INSTALL_PREFIX=/usr     \
          -DCMAKE_BUILD_TYPE=Release
}

build() {
    cd build
    make
}

do_install() {
    cd build
    make DESTDIR="$DESTDIR" install
}
