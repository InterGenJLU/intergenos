#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# lib32-libsndfile 1.2.2 — sound-file I/O library (32-bit multilib runtime,
# GE arc audio closure). Sibling: packages/desktop/libsndfile (identical
# tarball/version — RT-9 + LIB32-SOURCE-DRIFT locks). Custom because the
# sibling's BLFS ALAC sed is compile-load-bearing and the pure-yml lane has
# no pre-configure hook.
#
# Flag grounding (pinned 1.2.2 configure.ac, G3 — pin, never autodetect):
# - --disable-mpeg: lame/mpg123 were MEASURED OUT of the decided
#   lib32 closure (2026-07-02) — the twin must not grow those NEEDED edges.
#   Enforcement by construction: no lib32-lame/lib32-mpg123 exist, so a
#   leaked edge dangles and the Step 4.75 NEEDED-closure gate halts.
# - external-libs stay at the pinned default (on): flac/ogg/vorbis/
#   vorbisenc/opus ARE the measured closure this twin exists to carry.
# - --disable-alsa: alsa feeds only the sndfile-play example program,
#   which the lib-only twin neither builds (full-suite off) nor ships.
# - --disable-sqlite: regression-test-only dep (autodetect would silently
#   pick nothing today — pinned so it can never pick anything).
# - --disable-full-suite: programs/docs ship with the 64-bit sibling; the
#   twin is runtime libs + .pc only.

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    # BLFS required fixes, verbatim from the sibling (read before twinning):
    # the ALAC bool-typedef removal is compile-load-bearing; the lossy_comp
    # test sed touches test-only code and is kept for sibling parity.
    sed -i '/typedef enum/,/bool ;/d' src/ALAC/alac_{en,de}coder.c
    sed '/ogg_opus/,+1s/HAVE_[A-Z_]*/0/' -i tests/lossy_comp_test.c

    ./configure --prefix=/usr              \
                --host=${LIB32_HOST}       \
                --libdir=/usr/lib32        \
                --disable-silent-rules     \
                --disable-static           \
                --disable-mpeg             \
                --disable-alsa             \
                --disable-sqlite           \
                --disable-full-suite
}

build() {
    set -e
    # Plain make streams compile lines (RT-8/F2-a: the archive-time time64
    # log assertion needs visible compile evidence; --disable-silent-rules
    # above is the autotools half of that mandate).
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$PWD/m32root" install
    lib32_stage_libs "$PWD/m32root"
    lib32_assert_only_lib32
    lib32_env_end
}
