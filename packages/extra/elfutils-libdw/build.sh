#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# elfutils-libdw 0.194 — libdw (DWARF) from the elfutils source
#
# Additive companion to core elfutils (LFS-exact, libelf only — see
# package.yml). Same configure flags as the core recipe; full-tree make
# because libdw folds in libebl/backends/libdwfl/libdwelf objects; the
# install step takes ONLY the libdw surface so nothing overlaps the
# core package's files.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # The hardcoded -Werror trips under glibc 2.43+/gcc 15: C23 bsearch/
    # memchr return const-qualified pointers for const input. Upstream
    # fixed it post-0.194 in the "Fix const-correctness issues" sweep
    # (elfutils-devel, committed 2025-11-25); this patch mirrors that
    # commit's hunks for the files this package compiles
    # (libcpu/riscv_disasm.c + libdw/dwarf_getsrclines.c — the sweep's
    # readelf.c/debuginfod hunks cover tools this build never enters).
    patch -p1 < "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0001-c23-const-correctness-bsearch-memchr.patch"

    ./configure --prefix=/usr        \
        --disable-debuginfod         \
        --enable-libdebuginfod=dummy
}

build() {
    set -e
    # Build only libdw.so's subdir closure (libdw links libebl +
    # backends + libcpu + libdwelf + libdwfl archives) — the src/ tools
    # (readelf etc.) never enter the build, keeping the -Werror surface
    # to exactly what this package ships.
    make -C lib      -j${IGOS_JOBS}
    make -C libelf   -j${IGOS_JOBS}
    make -C libebl   -j${IGOS_JOBS}
    make -C libcpu   -j${IGOS_JOBS}
    make -C backends -j${IGOS_JOBS}
    make -C libdwelf -j${IGOS_JOBS}
    make -C libdwfl  -j${IGOS_JOBS}
    make -C libdwfl_stacktrace -j${IGOS_JOBS}
    make -C libdw    -j${IGOS_JOBS}
}

check() {
    set -e
    : # Test suite fails to build with glibc-2.43+, skip (matches core elfutils)
}

do_install() {
    set -e
    make -C libdw    DESTDIR="$DESTDIR" install
    make -C libdwfl  DESTDIR="$DESTDIR" install
    make -C libdwelf DESTDIR="$DESTDIR" install
    install -vDm644 config/libdw.pc "${DESTDIR}/usr/lib/pkgconfig/libdw.pc"
    rm -f "${DESTDIR}/usr/lib/libdw.a"

    # Fail loudly if the libdw surface did not land where declared, or
    # if this package grew into core elfutils' files.
    test -e "${DESTDIR}/usr/lib/libdw.so.1"
    test -e "${DESTDIR}/usr/include/elfutils/libdw.h"
    if [ -e "${DESTDIR}/usr/lib/libelf.so" ] || [ -e "${DESTDIR}/usr/lib/libelf.so.1" ]; then
        echo "FATAL: elfutils-libdw install grew libelf files - overlaps core elfutils" >&2
        exit 1
    fi
}
