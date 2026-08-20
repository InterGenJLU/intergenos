#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# spidermonkey 140.8.0 — Mozilla SpiderMonkey JavaScript engine
# BLFS 13.0
# Note: source is from firefox ESR tarball

configure() {
    set -e
    # Apply Python 3.14 compatibility patch

    mkdir -p obj &&
    cd    obj &&

    # NOTE: --enable-rust-simd intentionally OMITTED. Mozilla's simd-accel
    # feature in vendored encoding_rs uses feature(core_intrinsics,
    # portable_simd) which requires nightly Rust. We ship stable Rust 1.95.0,
    # which fails with E0599 in encoding_rs/x_user_defined.rs (Mask::select
    # moved behind Select trait). Standard distro practice on stable Rust.
    # Encoding remains correct via encoding_rs scalar fallback paths.
    CC=gcc CXX=g++ \
    ../js/src/configure --prefix=/usr            \
                        --disable-debug-symbols  \
                        --disable-jemalloc       \
                        --enable-readline        \
                        --with-intl-api          \
                        --with-system-icu        \
                        --with-system-zlib
}

build() {
    set -e
    cd obj &&
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # The blanket suite mask was retired here 2026-08-19: it accepted all
    # 50,000-plus results whatever they were. The suite still runs with the
    # same arguments; the tests: block in package.yml declares the policy and
    # pkg_run_tests reports the outcome, so a waiver is named in the log
    # rather than implied by silence.
    cd obj
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make -C js/src check-jstests \
             JSTESTS_EXTRA_ARGS="--timeout 300 --wpt=disabled"
}

do_install() {
    set -e
    cd obj &&

    # Remove old shared lib to avoid crash on reinstall
    rm -fv "${DESTDIR}/usr/lib/libmozjs-"*.so 2>/dev/null || true

    make DESTDIR="$DESTDIR" install

    # Remove static lib
    rm -v "${DESTDIR}/usr/lib/libjs_static.ajs" 2>/dev/null || true

    # Fix js config
    sed -i '/@NSPR_CFLAGS@/d' "${DESTDIR}/usr/bin/js"*-config 2>/dev/null || true

    # XP_UNIX define staged into the shipped header (hook-contract wave —
    # the retired live-system sed made js-config.h diverge from its own
    # archive: the ge9b-11 Step-2.7 finding). Fails loud if the header moves.
    jsver="${version%%.*}"
    sed "\$i#define XP_UNIX" -i "${DESTDIR}/usr/include/mozjs-${jsver}/js-config.h"
}

