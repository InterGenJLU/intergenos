#!/bin/bash
# lynx 2.9.2 — Text-mode web browser
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --sysconfdir=/etc/lynx \
                --with-zlib \
                --with-bzlib \
                --with-ssl \
                --with-screen=ncursesw \
                --enable-locale-charset
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
