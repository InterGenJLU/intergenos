#!/bin/bash
# alsa-utils 1.2.15.2 — ALSA utilities
# BLFS 13.0

configure() {
    ./configure --prefix=/usr         \
                --disable-alsaconf  \
                --disable-bat       \
                --disable-xmlto     \
                --with-curses=ncursesw \
                --with-udev-rules-dir=/usr/lib/udev/rules.d
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}

post_install() {
    udevadm control --reload 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
}
