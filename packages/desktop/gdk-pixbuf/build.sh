#!/bin/bash
# gdk-pixbuf 2.44.5 — Image loading library for GTK
# BLFS 13.0

configure() {
    set -e
    mkdir build
    cd    build

    # Pass 1: Build with built-in loaders but WITHOUT glycin.
    # glycin depends on librsvg which depends on gdk-pixbuf (circular).
    # gdk-pixbuf-pass2 rebuilds with glycin after librsvg is available.
    #
    # Built-in loaders (PNG/GIF/JPEG/TIFF) are required for GTK3 apps
    # that use gdk-pixbuf directly (glycin is GTK4-only).
    meson setup ..                \
          --prefix=/usr           \
          --libdir=/usr/lib       \
          --buildtype=release     \
          -Dpng=enabled           \
          -Dgif=enabled           \
          -Djpeg=enabled          \
          -Dtiff=enabled          \
          -Dthumbnailer=disabled  \
          -Dglycin=disabled       \
          --wrap-mode=nofallback
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
}

post_install() {
    set -e
    gdk-pixbuf-query-loaders --update-cache
}
