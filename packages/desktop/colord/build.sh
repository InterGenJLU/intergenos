#!/bin/bash
# colord 1.4.8 — Color management daemon
# BLFS 13.0

configure() {
    # Create colord system user/group
    groupadd -g 71 colord 2>/dev/null || true
    useradd -c "Color Daemon Owner" -d /var/lib/colord \
            -u 71 -g colord -s /bin/false colord 2>/dev/null || true

    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Ddocs=false \
          -Dman=false \
          -Ddaemon_user=colord \
          -Dvapi=true \
          -Dsystemd=true \
          -Dlibcolordcompat=true \
          -Dargyllcms_sensor=false \
          -Dbash_completion=false
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
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
