#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# dxvk 3.0 — Vulkan-based D3D for wine, cross-built PE both widths
# (GE extra-tier wave). Grounded against the pinned v3.0 tree (cross
# files, meson_options, .gitmodules read from the recursive clone) + the
# research doc in docs/sessions/. The shipped cross files pin our exact
# triplet binaries; needs_exe_wrapper is inert (no tests run).

configure() {
    set -e
    # Rule 5: the vendored submodule tree, every archive extracted
    # EXPLICITLY into its .gitmodules path (GitHub source tarballs ship
    # these directories empty). Paths + pins from the recursive clone of
    # the v3.0 tag.
    local sub
    while read -r sub tarball; do
        mkdir -p "${sub}"
        tar xf "${IGOS_SOURCES}/${tarball}" --strip-components=1 -C "${sub}"
    done <<'SUBMODULES'
include/native/directx dxvk-directx-headers-9df86f23.tar.gz
include/spirv dxvk-spirv-headers-04f10f65.tar.gz
include/vulkan dxvk-vulkan-headers-8864cdc8.tar.gz
subprojects/dxbc-spirv dxbc-spirv-aa18e0b0.tar.gz
subprojects/dxbc-spirv/submodules/spirv_headers spirv-headers-c8ad050f.tar.gz
subprojects/libdisplay-info dxvk-libdisplay-info-275e6459.tar.gz
SUBMODULES

    # Two full cross builds — one per PE width. DLLs land under
    # /usr/lib/dxvk/{x64,x32} via bindir/libdir (the GLFS shape).
    # build_id defaults false in the pinned meson_options (no build-id
    # nondeterminism); --strip uses the cross file's triplet strip —
    # never the host ELF strip on PE.
    local width cross
    for pair in "x64 build-win64.txt" "x32 build-win32.txt"; do
        set -- $pair; width=$1; cross=$2
        meson setup "build-${width}"                 \
              --cross-file "${cross}"                \
              --buildtype release                    \
              --strip                                \
              --prefix /usr                          \
              --bindir "lib/dxvk/${width}"           \
              --libdir "lib/dxvk/${width}"
    done
}

build() {
    set -e
    # -v mandatory on custom recipes (RT-8 compile-evidence mandate).
    ninja -v -C build-x64 -j${IGOS_JOBS}
    ninja -v -C build-x32 -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build-x64 install
    DESTDIR="$DESTDIR" ninja -C build-x32 install
}
