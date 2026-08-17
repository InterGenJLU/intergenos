#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# vkd3d-proton 3.0.1 — Vulkan-based D3D12 for wine, cross-built PE both
# widths (GE extra-tier wave). Grounded against the pinned v3.0.1 tree
# (cross files with the widl-mingw-tools-fallback binary, .gitmodules
# recursive set, the basedir-relative setup script — all read from the
# recursive clone) + the research doc in docs/sessions/.

configure() {
    set -e
    # Rule 5: the vendored recursive submodule tree, every archive
    # extracted EXPLICITLY into its .gitmodules path (incl. dxil-spirv's
    # own nested subprojects/third_party set).
    local sub
    while read -r sub tarball; do
        mkdir -p "${sub}"
        tar xf "${IGOS_SOURCES}/${tarball}" --strip-components=1 -C "${sub}"
    done <<'SUBMODULES'
khronos/SPIRV-Headers vkd3d-spirv-headers-f88a2d76.tar.gz
khronos/Vulkan-Headers vkd3d-vulkan-headers-ad9ce123.tar.gz
subprojects/dxil-spirv dxil-spirv-62dbb07f.tar.gz
subprojects/dxil-spirv/subprojects/dxbc-spirv dxbc-spirv-29c93aee.tar.gz
subprojects/dxil-spirv/subprojects/dxbc-spirv/submodules/spirv_headers spirv-headers-c8ad050f.tar.gz
subprojects/dxil-spirv/third_party/SPIRV-Cross spirv-cross-4b7bcb7e.tar.gz
subprojects/dxil-spirv/third_party/SPIRV-Tools spirv-tools-64f5770f.tar.gz
subprojects/dxil-spirv/third_party/spirv-headers vkd3d-spirv-headers-f88a2d76.tar.gz
SUBMODULES

    local width cross
    for pair in "x64 build-win64.txt" "x86 build-win32.txt"; do
        set -- $pair; width=$1; cross=$2
        meson setup "build-${width}"                     \
              --cross-file "${cross}"                    \
              --buildtype release                        \
              --strip                                    \
              --prefix /usr                              \
              --bindir "lib/vkd3d-proton/${width}"       \
              --libdir "lib/vkd3d-proton/${width}"
    done
}

build() {
    set -e
    # -v mandatory on custom recipes (RT-8 compile-evidence mandate).
    ninja -v -C build-x64 -j${IGOS_JOBS}
    ninja -v -C build-x86 -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build-x64 install
    DESTDIR="$DESTDIR" ninja -C build-x86 install

    # The first-party setup script, VERBATIM: its basedir resolution is
    # dirname($0) with x86/x64 defaults, so installed beside the two
    # width dirs it works unmodified (verified in the pinned script).
    # /usr/bin symlink for discoverability.
    install -m755 setup_vkd3d_proton.sh \
        "${DESTDIR}/usr/lib/vkd3d-proton/setup_vkd3d_proton.sh"
    install -dm755 "${DESTDIR}/usr/bin"
    ln -sf ../lib/vkd3d-proton/setup_vkd3d_proton.sh \
        "${DESTDIR}/usr/bin/setup_vkd3d_proton"
}
