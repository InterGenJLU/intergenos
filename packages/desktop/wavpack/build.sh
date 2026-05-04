#!/bin/bash
# wavpack 5.9.0 — Hybrid lossless/lossy audio compression library + CLI tools
# Upstream: https://www.wavpack.com/  (github.com/dbry/WavPack)
# License: BSD-3-Clause
# Provides: libwavpack.so, wavpack.pc, CLI: wavpack/wvunpack/wvgain/wvtag

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # Upstream wvtest performs encode → decode → md5 roundtrip on synthesized
    # PCM in memory. No audio device required, fully offline, deterministic.
    # Invoked via the autotools `check` target which runs cli/fast-tests.
    make check
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
