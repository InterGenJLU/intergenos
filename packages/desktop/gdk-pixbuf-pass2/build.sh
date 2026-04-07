#!/bin/bash
# gdk-pixbuf 2.44.5 — Pass 2 rebuild with glycin support
# BLFS 13.0
#
# Pass 1 builds gdk-pixbuf without glycin because glycin depends on
# librsvg which depends on gdk-pixbuf (circular dependency).
# After glycin and librsvg are installed, this pass rebuilds
# gdk-pixbuf with glycin enabled for GTK4 image loading.

configure() {
    mkdir build
    cd    build

    meson setup ..                \
          --prefix=/usr           \
          --libdir=/usr/lib       \
          --buildtype=release     \
          -Dpng=enabled           \
          -Dgif=enabled           \
          -Djpeg=enabled          \
          -Dtiff=enabled          \
          -Dthumbnailer=disabled  \
          -Dglycin=enabled        \
          --wrap-mode=nofallback
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    ninja install
}

post_install() {
    gdk-pixbuf-query-loaders --update-cache
}
