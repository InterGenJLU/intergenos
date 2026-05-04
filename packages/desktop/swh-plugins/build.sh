#!/bin/bash
# swh-plugins 0.4.17 — Steve Harris LADSPA plugin collection
#
# Ships ~100 LADSPA plugins (delay, reverb, EQ, distortion, comb, vocoder,
# etc.) — the de-facto "common" LADSPA plugin set used by Audacity and
# every other LADSPA host on Linux for the past two decades.
#
# Build notes:
#   - The release tarball ships pre-generated .c files but NOT a configure
#     script — we must run autoreconf -i first (per upstream README).
#   - --enable-sse turns on SSE codegen for FFTW; InterGenOS targets
#     x86_64 unconditionally so this is always safe.
#   - Mandatory dependency: fftw3f (32-bit-float FFTW3 build).
#   - Mandatory dependency: ladspa.h from ladspa-sdk.

configure() {
    autoreconf -fi
    ./configure --prefix=/usr  \
                --enable-sse
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
