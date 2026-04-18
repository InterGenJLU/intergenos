#!/bin/bash
# mokutil — Machine Owner Key management
# Required by: Forge installer (queue_mok_enrollment)

configure() {
    ./autogen.sh 2>/dev/null || autoreconf -fiv
    ./configure --prefix=/usr --sysconfdir=/etc
}

build() {
    make
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
