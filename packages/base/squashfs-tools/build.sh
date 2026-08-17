#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# squashfs-tools 4.7.5 — create/extract Squashfs filesystems (mksquashfs/unsquashfs)
#
# The Makefile lives in the squashfs-tools/ subdir of the upstream tree and uses
# a plain `cp`-based install (no DESTDIR support), so we override INSTALL_DIR /
# INSTALL_MANPAGES_DIR to land under $DESTDIR. All five compressors are enabled
# because every backing lib is in the tree (zlib, xz, zstd, lz4, lzo).

configure() {
    set -e
    # No ./configure — pure Makefile. Nothing to do here.
    :
}

build() {
    set -e
    cd squashfs-tools
    make -j"${IGOS_JOBS}" \
        GZIP_SUPPORT=1 \
        XZ_SUPPORT=1 \
        ZSTD_SUPPORT=1 \
        LZ4_SUPPORT=1 \
        LZO_SUPPORT=1 \
        XATTR_SUPPORT=1
}

do_install() {
    set -e
    cd squashfs-tools
    install -d "${DESTDIR}/usr/bin"
    install -d "${DESTDIR}/usr/share/man/man1"
    make install \
        INSTALL_DIR="${DESTDIR}/usr/bin" \
        INSTALL_MANPAGES_DIR="${DESTDIR}/usr/share/man/man1" \
        USE_PREBUILT_MANPAGES=y
}
