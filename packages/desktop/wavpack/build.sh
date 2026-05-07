#!/bin/bash
# wavpack 5.9.0 — Hybrid lossless/lossy audio compression library + CLI tools
# Upstream: https://www.wavpack.com/  (github.com/dbry/WavPack)
# License: BSD-3-Clause
# Provides: libwavpack.so, wavpack.pc, CLI: wavpack/wvunpack/wvgain/wvtag

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # Upstream wvtest performs encode → decode → md5 roundtrip on synthesized
    # PCM in memory. No audio device required, fully offline, deterministic.
    # Invoked via the autotools `check` target which runs cli/fast-tests.
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
