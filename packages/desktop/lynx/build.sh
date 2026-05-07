#!/bin/bash
# lynx 2.9.2 — Text-mode web browser
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --sysconfdir=/etc/lynx \
                --with-zlib \
                --with-bzlib \
                --with-ssl \
                --with-screen=ncursesw \
                --enable-locale-charset \
                --datadir=/usr/share/doc/lynx-2.9.2
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install-full
}

post_install() {
    set -e
    chgrp -v -R root /usr/share/doc/lynx-2.9.2/lynx_doc
}
