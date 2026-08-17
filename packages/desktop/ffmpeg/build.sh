#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
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
    # opt-in `ffmpeg-nonfree` package (see docs/legal/PATENTS.md
    # and audit P-015). The in-tree AAC encoder provides functional AAC
    # support without the FDK linkage.
    # NVENC/NVDEC (hardware video encode/decode on NVIDIA cards) are enabled
    # against the nv-codec-headers package, which supplies the `ffnvcodec`
    # pkg-config file. ffmpeg 8.0.1's configure requires ffnvcodec >= 12.1.14.0
    # (checked against the pinned upstream configure, lines 6912-6916); the
    # packaged headers report 13.1.15.0.1 and satisfy it.
    #
    # NO NVIDIA CODE IS LINKED OR REDISTRIBUTED BY THIS. Both paths dlopen the
    # driver's own libraries at runtime (libcuda.so.1, libnvidia-encode.so.1,
    # libnvcuvid.so.1); the headers only describe that interface. So this needs
    # no CUDA toolkit and no nvcc at build time, and the resulting binary runs
    # normally on a machine with no NVIDIA hardware — the encoder is simply not
    # available there.
    #
    # These are stated as EXPLICIT enables rather than left to autodetection on
    # purpose. Autodetection would silently produce an ffmpeg without hardware
    # encode if the headers were ever missing from the build environment, and a
    # capability that disappears quietly is exactly the class of defect the
    # explicit form turns into a configure-time failure.
    #
    # Deliberately NOT enabled: --enable-cuda-nvcc and --enable-libnpp. Both
    # require the CUDA toolkit at build time, which is not redistributable and
    # is not present in the build chroot.
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
                --enable-ffnvcodec   \
                --enable-nvenc       \
                --enable-nvdec       \
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
