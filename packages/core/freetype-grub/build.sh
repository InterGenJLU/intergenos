#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# freetype-grub 2.14.1 — minimal FreeType for Chapter 8.
#
# Purpose: GRUB only builds its console font (unicode.pf2) when FreeType is
# present at configure time (build-time grub-mkfont). LFS base does NOT ship
# FreeType, so the stock Ch8 GRUB shipped fontless and the gfxterm boot menu
# rendered missing-glyph "?"/"@" TOFU blocks. Building a minimal FreeType here,
# BEFORE grub, lets grub generate unicode.pf2 the canonical (BLFS) way.
#
# Minimal on purpose: HarfBuzz/PNG/Brotli disabled — grub-mkfont needs none of
# them to rasterize unifont. Only zlib (already built in Ch8) is used. The full
# freetype2 (HarfBuzz + PNG + Brotli) is rebuilt in the desktop tier and replaces
# this copy; this is a pass-0 in the same spirit as freetype2-pass1/freetype2.

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dharfbuzz=disabled \
          -Dbrotli=disabled   \
          -Dpng=disabled
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
