#!/bin/bash
# alsa-utils 1.2.15.2 — ALSA utilities
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr         \
                --disable-alsaconf  \
                --disable-bat       \
                --disable-xmlto     \
                --with-curses=ncursesw \
                --with-udev-rules-dir=/usr/lib/udev/rules.d
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}

post_install() {
    set -e
    udevadm control --reload 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
}
