#!/bin/bash
# lzip 1.26 — Lossless data compressor based on the LZMA algorithm
#
# Sibling to xz (Tukaani LZMA implementation) and bzip2 in the core
# archive-tool family. Required for any .tar.lz source extraction —
# `tar --lzip` shells out to lzip on PATH. ed 1.22.5 was the first
# in-tree consumer (GNU ed ships .tar.lz canonically); without lzip,
# ed's source extract halts with "tar (child): lzip: Cannot exec".
#
# Build: plain autotools, no exotic flags. C++98 + 'long long'
# (gcc 3.3.6+ — trivially satisfied by our gcc-15).

configure() {
    set -e
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make prefix="$DESTDIR/usr" install
}
