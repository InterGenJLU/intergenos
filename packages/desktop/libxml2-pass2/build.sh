#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libxml2-pass2 2.15.1 — pass 2 with the doxygen-generated Python bindings.
# Flags match packages/core/libxml2/build.sh exactly; the ONLY functional
# delta is doxygen's presence in the build environment, which lets meson
# schedule the doxygen_docs -> pygenerated -> libxml2mod target chain that
# pass 1 silently dropped (-Ddocs stays disabled: the bindings need only
# the doxygen XML, not the HTML docs — verified empirically, 32-target
# graph with docs disabled + doxygen present).

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          -Dhistory=enabled     \
          -Dicu=enabled         \
          -Dpython=enabled      \
          -Ddocs=disabled
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

    # Fail loud if the whole point of this pass is missing: the compiled
    # python module must be in the staged output (Rule 21 — never ship
    # the pass-1 silent-partial state again).
    ls "$DESTDIR"/usr/lib/python3.*/site-packages/libxml2mod*.so >/dev/null
    ls "$DESTDIR"/usr/lib/python3.*/site-packages/libxml2.py >/dev/null
}
