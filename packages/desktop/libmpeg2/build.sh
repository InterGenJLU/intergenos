#!/bin/bash
# libmpeg2 0.5.1 — Library for decoding MPEG-1 and MPEG-2 video streams
# Upstream: https://libmpeg2.sourceforge.io/
# BLFS 13.0 multimedia/libmpeg2

configure() {
    # BLFS: fix problems with recent GCC compilers ("static const" → "static"
    # in MMX IDCT inline-assembly operands, where 'const' is rejected by
    # current GCC for memory-operand vector tables).
    sed -i 's/static const/static/' libmpeg2/idct_mmx.c

    ./configure --prefix=/usr     \
                --enable-shared   \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
