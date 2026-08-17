#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# lib32-mesa 25.3.5 — 32-bit OpenGL/Vulkan stack (GE arc, Wave 2 — the twin
# this whole lane exists for). Sibling: packages/desktop/mesa (identical
# tarballs/version — RT-9 + LIB32-SOURCE-DRIFT locks).
#
# THE RT-3 BINDING RULE lands here: the feature matrix is fully explicit,
# duplicated in feature-matrix.json, and asserted between meson setup and
# ninja — a silently-auto-disabled surface (the missing-lib32-libxcb-kills-
# x11-WSI class) refuses the build instead of shipping. Driver surface:
# RADV + NVK (Rust — the reason the cross file exists) + intel Vulkan;
# radeonsi/iris real-HW GL + zink as the EXPLICIT fallback; swrast/llvmpipe
# deliberately absent (a broken 32-bit driver fails loudly, never
# software-renders). Trims (decided 2026-07-02): video/VA,
# rusticl, display-info, gles1.

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    # Pre-place the Rust crate tarballs (NVK) for the offline build —
    # same mechanism as the sibling; meson checks subprojects/packagecache.
    if [ -f "${IGOS_SOURCES}/mesa-${PKG_VERSION}-rust-crates.tar.gz" ]; then
        mkdir -p subprojects/packagecache
        tar -xf "${IGOS_SOURCES}/mesa-${PKG_VERSION}-rust-crates.tar.gz" \
            -C subprojects/packagecache
    fi

    mkdir -p build
    cd    build

    # The cross file pins compilers/rustc-target/pkg-config-personality/
    # llvm-config32 (RT-7); the matrix below is duplicated in
    # feature-matrix.json and asserted by the checker call after setup.
    meson setup ..                                                        \
          --cross-file /mnt/intergenos/config/lib32/lib32-cross.ini      \
          --prefix=/usr                                                   \
          --libdir=/usr/lib32                                             \
          --buildtype=release                                             \
          --wrap-mode=nodownload                                          \
          -D platforms=x11,wayland                                        \
          -D gallium-drivers=radeonsi,iris,zink                           \
          -D vulkan-drivers=amd,nouveau,intel                             \
          -D egl=enabled                                                  \
          -D glx=dri                                                      \
          -D gbm=enabled                                                  \
          -D glvnd=enabled                                                \
          -D gles1=disabled                                               \
          -D gles2=enabled                                                \
          -D llvm=enabled                                                 \
          -D xmlconfig=enabled                                            \
          -D zstd=enabled                                                 \
          -D expat=enabled                                                \
          -D display-info=disabled                                        \
          -D xlib-lease=enabled                                           \
          -D gallium-va=disabled                                          \
          -D lmsensors=disabled                                           \
          -D valgrind=disabled                                            \
          -D video-codecs=                                                \
          -D libunwind=disabled                                           \
          -D gallium-rusticl=false                                        \
          -D mesa-clc=system                                              \
          -D precomp-compiler=system

    python3 /mnt/intergenos/igos-build/mesa_feature_matrix.py \
        --build . \
        --matrix /mnt/intergenos/packages/desktop/lib32-mesa/feature-matrix.json \
        --label lib32-mesa
}

build() {
    set -e
    cd build
    # -v MANDATORY off the pure-yml lane: the archive-time time64 log
    # assertion refuses a log with no visible compile evidence (RT-8/F2-a).
    ninja -v
}

do_install() {
    set -e
    cd build
    DESTDIR="$PWD/m32root" ninja install

    # Allowlist-stage /usr/lib32 + the three i686 Vulkan ICD manifests as
    # DECLARED extras (mesa names them <drv>_icd.i686.json because the
    # cross file sets cpu=i686; library_path inside resolves to /usr/lib32
    # via prefix/libdir — verified in the pinned source). The glvnd vendor
    # json (50_mesa.json) is arch-independent and ships with the 64-bit
    # sibling — NOT re-shipped here (the assert would halt on it).
    lib32_stage_libs "$PWD/m32root" \
        usr/share/vulkan/icd.d/radeon_icd.i686.json \
        usr/share/vulkan/icd.d/nouveau_icd.i686.json \
        usr/share/vulkan/icd.d/intel_icd.i686.json
    lib32_assert_only_lib32 \
        usr/share/vulkan/icd.d/radeon_icd.i686.json \
        usr/share/vulkan/icd.d/nouveau_icd.i686.json \
        usr/share/vulkan/icd.d/intel_icd.i686.json
    lib32_env_end
}
