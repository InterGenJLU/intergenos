#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libva-utils 2.23.0 — VA-API utility programs (vainfo + friends)
#
# Build profile: AUTOTOOLS (configure + make + make install). The 2.23.0
# release tarball (releases/download/2.23.0/libva-utils-2.23.0.tar.bz2) ships a
# pre-generated `configure` (656 KB) with configure.ac + Makefile.am — and NO
# meson.build anywhere (verified against the pinned tarball 2026-06-02). No
# autogen.sh/autoreconf needed. configure picks up libva/libdrm/libX11 via
# pkg-config and builds vainfo + vaplay + vapostproc.
#
# NOTE (2026-06-02, build-rules §2.8 upstream-drift / stale-recipe): this recipe
# previously declared build_style: meson + `meson setup ..`, on the assumption
# that libva-utils uses meson (conflated with libva, which does). The pinned
# 2.23.0 RELEASE asset is autotools-only — it halted the build at configure with
# "Neither source directory '..' nor build directory contain meson.build". Fixed
# to match the ACTUAL artifact, not the distro-reference assumption.
#
# Security-only-alignment filter: command-line utilities, no SUID, no daemon, no
# network surface, no persistent state. Safe.

configure() {
    set -e
    ./configure                 \
          --prefix=/usr         \
          --libdir=/usr/lib
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
