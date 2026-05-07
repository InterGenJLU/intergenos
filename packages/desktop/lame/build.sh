#!/bin/bash
# lame 3.100 — MP3 encoder
# BLFS 13.0

configure() {
    set -e
    # BLFS: prevent hardcoded library search path
    sed -i -e 's/^\(\s*hardcode_libdir_flag_spec\s*=\).*/\1/' configure

    ./configure --prefix=/usr \
                --enable-mp3rtp \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
