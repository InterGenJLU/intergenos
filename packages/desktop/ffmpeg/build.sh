#!/bin/bash
# ffmpeg 8.0.1 — Complete multimedia framework
# BLFS 13.0

configure() {
    set -e
    # Apply chromium method patch

    # Fix for SVT-AV1 4.0.0+
    sed -e '/adaptive/c\ param->aq_mode = 0;' \
        -i libavcodec/libsvtav1.c

    # Default ffmpeg build — REDISTRIBUTABLE under LGPL-2.1+ with --enable-gpl
    # for x264/x265 (themselves GPL-2). Patent-encumbered nonfree codecs
    # (FDK-AAC) are NOT linked here; they are available via the
    # opt-in `ffmpeg-nonfree-helper` package (see docs/legal/PATENTS.md
    # and audit P-015). The in-tree AAC encoder provides functional AAC
    # support without the FDK linkage.
    ./configure --prefix=/usr        \
                --enable-gpl         \
                --enable-version3    \
                --disable-static     \
                --enable-shared      \
                --disable-debug      \
                --enable-libaom      \
                --enable-libass      \
                --enable-libfreetype \
                --enable-libmp3lame  \
                --enable-libopus     \
                --enable-libvorbis   \
                --enable-libvpx      \
                --enable-libx264     \
                --enable-libx265     \
                --enable-openssl     \
                --enable-libdav1d    \
                --enable-libsvtav1   \
                --enable-encoder=aac \
                --docdir=/usr/share/doc/ffmpeg-${version}
}

build() {
    set -e
    make -j${IGOS_JOBS} &&
    gcc tools/qt-faststart.c -o tools/qt-faststart
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    install -v -m755    tools/qt-faststart "${DESTDIR}/usr/bin"
    install -v -m755 -d           "${DESTDIR}/usr/share/doc/ffmpeg-${version}"
    install -v -m644    doc/*.txt "${DESTDIR}/usr/share/doc/ffmpeg-${version}"
}
