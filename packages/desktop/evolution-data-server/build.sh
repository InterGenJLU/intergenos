#!/bin/bash
# evolution-data-server 3.58.3 — Calendar and contacts data server
# BLFS 13.0

configure() {
    cmake -B build -G Ninja                    \
          -DCMAKE_INSTALL_PREFIX=/usr          \
          -DCMAKE_BUILD_TYPE=Release           \
          -DSYSCONF_INSTALL_DIR=/etc           \
          -DENABLE_GTK_DOC=OFF                 \
          -DENABLE_INSTALLED_TESTS=OFF         \
          -DENABLE_VALA_BINDINGS=ON            \
          -DENABLE_INTROSPECTION=ON            \
          -DWITH_OPENLDAP=OFF                  \
          -DWITH_KRB5=OFF                      \
          -DWITH_LIBDB=OFF                     \
          -W no-dev
}

build() {
    ninja -C build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" ninja -C build install
}
