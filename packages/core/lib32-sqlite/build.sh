#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# Sqlite 3510200 — 32-bit multilib runtime (GE arc, nss forced closure)
# Sibling: packages/core/sqlite (same tarball, same version — RT-9 lock).
# Profile: scripts/lib32-env.sh.

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    # Sibling parity: same soname pin, same feature flags, same four
    # CPPFLAGS defines — only the host triplet + lib32 libdir differ.
    LDFLAGS="-Wl,-soname,libsqlite3.so.0" \
    ./configure --prefix=/usr     \
        --host=${LIB32_HOST}      \
        --libdir=/usr/lib32       \
        --disable-static          \
        --enable-fts4             \
        --enable-fts5             \
        CPPFLAGS="-DSQLITE_ENABLE_COLUMN_METADATA=1 \
                  -DSQLITE_ENABLE_UNLOCK_NOTIFY=1   \
                  -DSQLITE_ENABLE_DBSTAT_VTAB=1     \
                  -DSQLITE_SECURE_DELETE=1"
}

build() {
    set -e
    make LDFLAGS.rpath="" -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$PWD/m32root" install
    lib32_stage_libs "$PWD/m32root"
    lib32_assert_only_lib32
    lib32_env_end
}
