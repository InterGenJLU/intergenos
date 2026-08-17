#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# handbrake 1.11.2 — video transcoder (HandBrakeCLI + the ghb GTK front-end).
#
# OFFLINE CONTRIB STAGING
# -----------------------
# HandBrake's build downloads seven source tarballs of its own and builds them
# into the transcoder: ffmpeg, x265, dav1d, SVT-AV1, zimg, libdvdread,
# libdvdnav and libbluray. On Linux these are not optional and there is no
# system-library switch for them — the ffmpeg it uses carries patches that are
# not upstream, and x265 is compiled three extra times for 8/10/12-bit depth.
#
# The build chroot has no network, so each tarball is declared as a secondary
# source in package.yml (fetched and sha256-checked by the builder like any
# other source) and copied here into the distfile directory the build reads.
# Downloads are then disabled with --disable-df-fetch. VERIFICATION IS LEFT
# ON: HandBrake re-checks every staged tarball against its own recorded
# sha256, so the bytes are checked twice by two independent pins rather than
# waved through.
#
# The remaining codec libraries (libass, x264, theora, vorbis, ogg, lame,
# opus, speex, vpx, jansson, turbojpeg, freetype, harfbuzz, fribidi) come from
# this tree; upstream expects them from the system on Linux and does not build
# contrib copies.
#
# FEATURE POSTURE
# ---------------
# Enabled: the GTK front-end, x265 encoding and its NUMA support.
# Disabled: fdk-aac (non-free AAC encoder — the build stays GPL-clean and uses
# ffmpeg's native AAC encoder), libdovi, and every vendor hardware-encode path
# (NVENC/NVDEC, Intel QSV, AMD VCE), each of which needs vendor SDK headers
# that are not in the tree. Nothing here disables a feature to work around a
# missing dependency that could have been packaged: each disabled item is a
# licence or vendor-SDK decision, recorded in package.yml.

CONTRIB_TARBALLS="
ffmpeg-8.0.2.tar.bz2
x265-snapshot-20260216-13309.tar.gz
dav1d-1.5.3.tar.bz2
SVT-AV1-v4.1.0.tar.gz
zimg-snapshot-20250624.tar.gz
libdvdread-7.0.1.tar.bz2
libdvdnav-7.0.0.tar.bz2
libbluray-1.4.0.tar.xz
"

configure() {
    set -e

    # Stage the pinned contrib tarballs where the build expects distfiles.
    # A missing one is fatal here rather than at the point the build would
    # otherwise try to reach the network.
    mkdir -p download
    for tarball in ${CONTRIB_TARBALLS}; do
        if [ ! -f "${IGOS_SOURCES}/${tarball}" ]; then
            echo "handbrake: contrib tarball not staged: ${tarball}" >&2
            exit 1
        fi
        cp -f "${IGOS_SOURCES}/${tarball}" "download/${tarball}"
    done

    ./configure                 \
        --build build           \
        --prefix=/usr           \
        --disable-df-fetch      \
        --enable-gtk            \
        --enable-x265           \
        --enable-numa           \
        --disable-fdk-aac       \
        --disable-nvenc         \
        --disable-nvdec         \
        --disable-qsv           \
        --disable-vce           \
        --disable-libdovi       \
        --force
}

build() {
    set -e
    make -C build -j${IGOS_JOBS}
}

do_install() {
    set -e
    make -C build DESTDIR="$DESTDIR" install
}

post_install() {
    set -e
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
