#!/bin/bash
# vorbis-tools 1.4.3 — Vorbis codec command-line tools (oggenc, oggdec, etc.)
# Required by: gnome-clocks (alarm sound .ogg encoding at build time)

configure() {
    ./configure --prefix=/usr
}

build() {
    make
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
