#!/bin/bash
# mokutil — Machine Owner Key management
# Required by: Forge installer (queue_mok_enrollment)

configure() {
    set -e
    ./autogen.sh 2>/dev/null || autoreconf -fiv
    ./configure --prefix=/usr --sysconfdir=/etc
}

build() {
    set -e
    make
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
