#!/bin/bash
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
#   - DESTDIR is preserved across both `make install` invocations, so both
#     library sets land in the same staging tree for pkm to package.
#   - SIMD flags (sse2/avx/avx2) are upstream-recommended for x86_64; runtime
#     dispatch picks the best path for the host CPU at execution time.

configure() {
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
    # Pass 1: float — build then immediately stage-install so the artefacts
    # survive `make distclean` before pass 2.
    make -j${IGOS_JOBS}
    make DESTDIR="$DESTDIR" install

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
    # Install pass 2 (double) — pass 1 (float) was already staged in build().
    make DESTDIR="$DESTDIR" install
}
