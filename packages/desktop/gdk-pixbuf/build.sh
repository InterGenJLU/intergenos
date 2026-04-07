#!/bin/bash
# gdk-pixbuf 2.44.5 — Image loading library for GTK
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    # BLFS 13.0 disables built-in loaders in favor of glycin, but glycin
    # only works with GTK4/GNOME apps. GTK3 apps (gnome-terminal, etc.)
    # still use gdk-pixbuf directly and need the built-in loaders.
    # Enable PNG at minimum — it's required for icon rendering.
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
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    gdk-pixbuf-query-loaders --update-cache
}
