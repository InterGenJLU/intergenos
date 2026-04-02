#!/bin/bash
# evolution-data-server 3.54.3 — Calendar and contacts data server
# BLFS 13.0

configure() {
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DENABLE_GTK_DOC=OFF \
          -DENABLE_INSTALLED_TESTS=OFF \
          -DENABLE_OAUTH2_WEBKITGTK4=ON
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
