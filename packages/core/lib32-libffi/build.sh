#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# Libffi 3.5.2 — 32-bit multilib runtime (GE arc, Wave 1)
# Sibling: packages/core/libffi (same tarball, same version — RT-9 lock).
# The sibling's --with-gcc-arch=native is deliberately NOT carried: native
# detection under -m32 on the build host would bake host-specific arch into
# a runtime lib for prebuilt-binary consumers; the chroot baseline CFLAGS
# govern instead. Profile: scripts/lib32-env.sh.

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    ./configure --prefix=/usr        \
        --host=${LIB32_HOST}         \
        --libdir=/usr/lib32          \
        --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$PWD/m32root" install
    lib32_stage_libs "$PWD/m32root"
    lib32_assert_only_lib32
    lib32_env_end
}
