#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# v4l-utils 1.32.0 — Video4Linux utilities and libraries
#
# v4l2-ctl / media-ctl are the capture-stack ground-truth instruments
# (device enumeration, format/control inspection — the "captured frame"
# acceptance evidence), and libv4l is the format-conversion compatibility
# layer applications use to consume exotic camera pixel formats.

configure() {
    set -e
    mkdir -p build
    cd    build

    # Explicit feature flags (the pipewire silent-auto lesson): jpeg
    # =enabled halts if libjpeg-turbo ever goes missing instead of
    # silently dropping MJPEG conversion. The two Qt GUIs (qv4l2,
    # qvidcap) are =disabled as a scoping decision, not a dep bypass:
    # the distribution ships no Qt stack, and both tools are diagnostic
    # GUIs whose CLI equivalents (v4l2-ctl) ship here; mainstream
    # distributions split them into a separate Qt package for the same
    # reason. bpf=disabled: IR-remote protocol decoders needing
    # libbpf+clang at build; IR decoding is outside the capture-stack
    # scope and libbpf is not in the tree (surface as its own authoring
    # candidate if IR support is ever mandated).
    # gconv=enabled (was silently auto): the DVB broadcast charset
    # converters (ARIB-STD-B24, EN300-468-TAB00) for the dvbv5 tools.
    # Explicit so their disappearance is a halt, not a silent drop —
    # and do_install below MUST relocate their config fragment.
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Djpeg=enabled      \
          -Dgconv=enabled     \
          -Dqv4l2=disabled    \
          -Dqvidcap=disabled  \
          -Dbpf=disabled      \
          -Dv4l-utils=true    \
          -Dv4l-plugins=true  \
          -Dv4l-wrappers=true \
          -Dudevdir=/usr/lib/udev
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

    # meson installs the gconv config as /usr/lib/gconv/gconv-modules —
    # glibc's MASTER charset table. Installed as-is it clobbers glibc's
    # copy and every basic charset (ISO-8859-1 first) stops resolving.
    # Relocate to glibc's fragment dir, the surface meant for
    # third-party modules; the mv fails loudly if the file ever moves.
    install -d "$DESTDIR/usr/lib/gconv/gconv-modules.d"
    mv "$DESTDIR/usr/lib/gconv/gconv-modules" \
       "$DESTDIR/usr/lib/gconv/gconv-modules.d/gconv-modules-v4l.conf"
}
