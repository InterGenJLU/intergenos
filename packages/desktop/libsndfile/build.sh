#!/bin/bash
# libsndfile 1.2.2 — Library for reading and writing sound files
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i '/typedef enum/,/bool ;/d' src/ALAC/alac_{en,de}coder.c
    sed '/ogg_opus/,+1s/HAVE_[A-Z_]*/0/' -i tests/lossy_comp_test.c
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
