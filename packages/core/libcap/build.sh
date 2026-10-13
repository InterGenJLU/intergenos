#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Libcap 2.77
# LFS 13.0 Section 8.27
#
# DESTDIR exception: needs prefix=, lib=, and RAISE_SETFCAP=no for staging.

configure() {
    set -e
    # Prevent static library installation
    sed -i '/install -m.*STA/d' libcap/Makefile
}

build() {
    set -e
    # captree is a Go program; libcap builds and installs it only when a Go
    # toolchain is on PATH (Make.Rules auto-detects `go`). A from-scratch
    # Chapter 8 build has none, so the package never carried it; a resume
    # over a populated build root does, and the archive gained
    # usr/sbin/captree. Pin the detection off so every build shape produces
    # the same archive.
    make prefix=/usr lib=lib GOLANG=no -j${IGOS_JOBS}
}

check() {
    set -e
    make GOLANG=no test
}

do_install() {
    set -e
    make prefix=/usr lib=lib GOLANG=no DESTDIR="$DESTDIR" RAISE_SETFCAP=no install
}
