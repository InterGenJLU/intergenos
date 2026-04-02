#!/bin/bash
# webkitgtk 2.46.6 — Web content engine for GTK
# BLFS 13.0

configure() {
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release \
          -DENABLE_MINIBROWSER=ON \
          -DUSE_GTK4=ON \
          -DENABLE_DOCUMENTATION=OFF \
          -DUSE_LIBBACKTRACE=OFF
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
