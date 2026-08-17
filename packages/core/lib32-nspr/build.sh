#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# NSPR 4.38.2 — 32-bit multilib runtime (GE arc, nss forced closure)
# Sibling: packages/core/nspr (same tarball, same version — RT-9 lock).
# Profile: scripts/lib32-env.sh.
# Note: source extracts with nspr/ subdirectory (sibling parity).

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    cd nspr

    # Sibling parity: disable installing unneeded static libs and the
    # compile-analysis scripts (same two seds as the 64-bit recipe).
    sed -i '/^RELEASE/s|^|#|' pr/src/misc/Makefile.in
    sed -i 's|$(LIBRARY) ||'  config/rules.mk

    # 32-bit via NSPR's own --disable-64bit (the sibling's uname-gated
    # --enable-64bit, inverted — the Arch lib32-nspr shape), agreeing
    # with the profile's -m32 CC: both mechanisms select the same ABI.
    ./configure --prefix=/usr    \
                --libdir=/usr/lib32 \
                --host=${LIB32_HOST} \
                --with-mozilla   \
                --with-pthreads  \
                --disable-64bit
}

build() {
    set -e
    cd nspr
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd nspr
    make DESTDIR="$PWD/m32root" install
    lib32_stage_libs "$PWD/m32root"
    lib32_assert_only_lib32
    lib32_env_end
}
