#!/bin/bash
# gdk-pixbuf 2.44.5 — Image loading library for GTK
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..                \
          --prefix=/usr           \
          --buildtype=release     \
          -Dpng=disabled          \
          -Dgif=disabled          \
          -Djpeg=disabled         \
          -Dtiff=disabled         \
          -Dthumbnailer=disabled  \
          -Dglycin=disabled       \
          --wrap-mode=nofallback
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
    gdk-pixbuf-query-loaders --update-cache
}
