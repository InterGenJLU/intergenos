#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# fftw 3.3.11 — Fastest Fourier Transform in the West
#
# Two-pass build: float (fftw3f) + double (fftw3)
# ------------------------------------------------
# fftw upstream ships precision as a build-time decision: a single source tree
# can produce libraries for single precision (float, --enable-float), double
# precision (the default, no flag), or extended precision (--enable-long-double).
# Each pass installs a distinct shared library + pkg-config file:
#
#     pass 1 (float):   /usr/lib/libfftw3f.so   /usr/lib/pkgconfig/fftw3f.pc
#     pass 2 (double):  /usr/lib/libfftw3.so    /usr/lib/pkgconfig/fftw3.pc
#
# Why both?
#   - swh-plugins (LADSPA effects collection) and most real-time audio code
#     consume libfftw3f (float — fast, cache-friendly, plenty of precision for
#     audio).
#   - Scientific / numerical code conventionally uses libfftw3 (double).
#   - Shipping both up front means future consumers of either flavour link
#     without us needing to revisit the package. This is exactly the pattern
#     BLFS prescribes for fftw.
#
# Why NOT long-double?
#   - It's used by a tiny minority of scientific consumers, adds a third full
#     compile pass (~33% extra build time), and we have no current consumer.
#     Easy to add a third pass later if a real consumer arrives.
#
# Implementation note
#   - "make distclean" between passes resets configure state cleanly so the
#     second run picks up the new --enable-float toggle without stale cache.
#   - The float pass installs during build(), where the builder scopes DESTDIR
#     OUT of the environment (a build-phase install must never see the packaging
#     DESTDIR, or it is silently redirected). So the float pass stage-installs to
#     a recipe-LOCAL dir (.fftw-float-stage) to survive `make distclean` before
#     pass 2; do_install merges that stage into the packaging DESTDIR alongside
#     the double pass.
#   - SIMD flags (sse2/avx/avx2) are upstream-recommended for x86_64; runtime
#     dispatch picks the best path for the host CPU at execution time.

configure() {
    set -e
    # First pass: single-precision (float / fftw3f)
    ./configure --prefix=/usr        \
                --enable-shared      \
                --disable-static     \
                --enable-threads     \
                --enable-sse2        \
                --enable-avx         \
                --enable-avx2        \
                --enable-float
}

build() {
    set -e
    # Pass 1: float — build then stage-install to a recipe-LOCAL dir so the
    # artefacts survive `make distclean` before pass 2. An EXPLICIT local DESTDIR
    # (not the ambient one, which the builder strips from the build phase) keeps
    # the float tree here for do_install to merge into the packaging DESTDIR.
    make -j${IGOS_JOBS}
    make DESTDIR="$PWD/.fftw-float-stage" install

    # Pass 2: double precision (default — omit --enable-float)
    make distclean
    ./configure --prefix=/usr        \
                --enable-shared      \
                --disable-static     \
                --enable-threads     \
                --enable-sse2        \
                --enable-avx         \
                --enable-avx2
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    # Merge the float pass (staged locally in build(), because a build-phase
    # install cannot see the packaging DESTDIR) FIRST, then install the double
    # pass over it — so the double build stays authoritative on the two passes'
    # only shared, precision-independent file (the umbrella header fftw3.h),
    # reproducing the prior float-then-double single-DESTDIR staging exactly. The
    # float-only artefacts (libfftw3f.so, fftw3f.pc, fftwf-* tools) are additive.
    # Fail loud if build() did not produce the stage.
    [ -d "$PWD/.fftw-float-stage/usr" ] || {
        echo "FATAL: fftw float stage missing at $PWD/.fftw-float-stage/usr — build() did not stage the float pass" >&2
        exit 1
    }
    cp -a "$PWD/.fftw-float-stage/usr/." "$DESTDIR/usr/"
    make DESTDIR="$DESTDIR" install
}
