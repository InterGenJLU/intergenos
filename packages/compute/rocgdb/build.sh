#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2026 InterGenJLU
#
# rocgdb 7.2.4 — GPU-aware GDB fork (autotools)
# Source: standalone ROCm/ROCgdb repo at the rocm-7.2.4 tag
#
# Configure flags = the pinned README-ROCM.md recipe verbatim, plus
# --prefix=/opt/rocm and the rocdbgapi pkg-config path. The dbgapi
# library lives on the shipped loader path (/etc/ld.so.conf.d/rocm.conf
# via rocm-hip), so no rpath injection is needed at runtime.
#
# librocm-dbgapi.so carries no RUNPATH and its DT_NEEDED closure
# (libamd_comgr.so.3) lives in /opt/rocm/lib, which enters the loader
# path only once rocm-hip installs rocm.conf — a package this one does
# NOT depend on and which builds AFTER it on a from-scratch graph, so
# configure's amd-dbgapi link test cannot resolve the closure there.
# -rpath-link supplies the closure path at LINK TIME ONLY (nothing is
# written into the produced binaries); runtime resolution still comes
# from the shipped rocm.conf.

configure() {
    set -e
    export PKG_CONFIG_PATH="/opt/rocm/share/pkgconfig:/opt/rocm/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    export LDFLAGS="-Wl,-rpath-link,/opt/rocm/lib ${LDFLAGS:-}"

    mkdir -p build
    cd build
    ../configure \
        --prefix=/opt/rocm \
        --program-prefix=roc \
        --enable-64-bit-bfd \
        --enable-targets="x86_64-linux-gnu,amdgcn-amd-amdhsa" \
        --disable-ld --disable-gas --disable-gdbserver --disable-sim \
        --enable-tui --disable-gdbtk --disable-gprofng --disable-shared \
        --with-expat --with-system-zlib --with-system-readline \
        --without-guile --without-babeltrace --with-lzma \
        --with-python=python3
}

build() {
    set -e
    export PKG_CONFIG_PATH="/opt/rocm/share/pkgconfig:/opt/rocm/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    export LDFLAGS="-Wl,-rpath-link,/opt/rocm/lib ${LDFLAGS:-}"
    cd build
    make -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd build
    make install DESTDIR="$DESTDIR"
}
