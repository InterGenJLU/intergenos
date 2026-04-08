#!/bin/bash
# lame 3.100 — Pass 2 rebuild with libsndfile support
# BLFS 13.0
#
# Pass 1 builds LAME without libsndfile because libsndfile depends
# on LAME for MP3 encoding (circular dependency). After libsndfile
# is installed, this pass rebuilds LAME with libsndfile support,
# enabling direct encoding from FLAC, AIFF, OGG, and other formats.

configure() {
    # BLFS: prevent hardcoded library search path
    sed -i -e 's/^\(\s*hardcode_libdir_flag_spec\s*=\).*/\1/' configure

    ./configure --prefix=/usr \
                --enable-mp3rtp \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
