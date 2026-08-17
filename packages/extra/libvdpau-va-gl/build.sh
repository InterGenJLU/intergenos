#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libvdpau-va-gl 0.4.2 — VDPAU-on-VAAPI translation driver
#
# Build profile: cmake out-of-source. Upstream's CMakeLists is
# minimal — sets up the source list + LIB_INSTALL_DIR + DRIVER_NAME.
# We override LIB_INSTALL_DIR to land at /usr/lib/vdpau (matching
# libvdpau's default dispatcher search path).
#
# Configure flags chosen:
#   -DCMAKE_BUILD_TYPE=Release   optimized; no debug shipped
#   -DCMAKE_INSTALL_PREFIX=/usr  standard distro layout
#   -DCMAKE_INSTALL_LIBDIR=lib   avoid lib64/lib pkg-config split
#   (LIB_INSTALL_DIR derives from INSTALL_PREFIX + lib + vdpau —
#    upstream Cache var, picked up automatically)
#   -DCMAKE_POLICY_VERSION_MINIMUM=3.5  upstream CMakeLists.txt declares
#    cmake_minimum_required(VERSION 2.8); cmake 4.x removed <3.5 compat.
#    Matches the tree-wide class fix (44 other packages carry this flag).
#
# Cross-distro flag comparison:
#   Arch:   cmake -DCMAKE_INSTALL_PREFIX=/usr (defaults; no LIB_INSTALL_DIR override)
#   Fedora: %cmake macros (same effective result)
#   Debian: dh_auto_configure + CMake (same)
# All three rely on upstream's default LIB_INSTALL_DIR which resolves
# to ${PREFIX}/lib/vdpau. We align.
#
# Security-only-alignment filter: a VDPAU back-end driver loaded by the libvdpau
# dispatcher via dlopen() when an app sets VDPAU_DRIVER=va_gl. The
# .so itself has no SUID surface, no network, no setuid capabilities.
# Translates VDPAU calls to VAAPI + GL calls — the trust boundary is
# the underlying VAAPI driver (radeonsi VAAPI in Mesa, which is what
# the kernel module signs + dm-verity validates).
#
# Builds an .so + symlink (libvdpau_va_gl.so → libvdpau_va_gl.so.1).
# No headers installed (this is a driver, not a library to link
# against).

configure() {
    set -e
    cmake -B build                                                  \
          -DCMAKE_BUILD_TYPE=Release                                \
          -DCMAKE_INSTALL_PREFIX=/usr                               \
          -DCMAKE_INSTALL_LIBDIR=lib                                \
          -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
