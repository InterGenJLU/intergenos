#!/bin/bash
# ffmpeg 8.0.1 — Complete multimedia framework
# BLFS 13.0

configure() {
    # Apply chromium method patch

    # Fix for SVT-AV1 4.0.0+
    sed -e '/adaptive/c\ param->aq_mode = 0;' \
        -i libavcodec/libsvtav1.c

    ./configure --prefix=/usr        \
                --enable-gpl         \
                --enable-version3    \
                --enable-nonfree     \
                --disable-static     \
                --enable-shared      \
                --disable-debug      \
                --enable-libaom      \
                --enable-libass      \
                --enable-libfdk-aac  \
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
                --docdir=/usr/share/doc/ffmpeg-${version}
}

build() {
    make -j${IGOS_JOBS} &&
    gcc tools/qt-faststart.c -o tools/qt-faststart
}

do_install() {
    make DESTDIR="$DESTDIR" install

    install -v -m755    tools/qt-faststart "${DESTDIR}/usr/bin"
    install -v -m755 -d           "${DESTDIR}/usr/share/doc/ffmpeg-${version}"
    install -v -m644    doc/*.txt "${DESTDIR}/usr/share/doc/ffmpeg-${version}"
}
