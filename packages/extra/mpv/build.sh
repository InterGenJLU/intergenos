#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# mpv 0.41.0 — Free media player for the command line and desktop
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..                \
          --prefix=/usr           \
          --libdir=/usr/lib       \
          --buildtype=release     \
          -Dx11=enabled           \
          -Dwayland=enabled       \
          -Ddvdnav=enabled        \
          -Dcdda=enabled
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

    # mpv is a backend for Celluloid — hide it from the application menu.
    #
    # The key belongs in the PACKAGE. It used to be appended by post_install,
    # which on the python tiers runs on the live tree after the archive has
    # already been made, and with DESTDIR stripped from the environment
    # (igos-build/builder.py phase_env). So the append never reached an
    # archive: in the build chroot it edited the freshly deployed copy, and on
    # a target the installer's re-run edited the installed file — an unowned
    # mutation of a path mpv's own manifest claims. Confirmed against the
    # shipped artifacts, which carry no NoDisplay key at all, and on an
    # installed system, whose copy carries exactly one appended key.
    #
    # Written here it is archived, owned and verifiable. mpv.desktop is a
    # single [Desktop Entry] group, so appending places the key in the right
    # section; an absent file or an upstream that starts shipping its own key
    # is surfaced rather than silently doubled.
    local desktop="${DESTDIR}/usr/share/applications/mpv.desktop"
    if [ ! -f "$desktop" ]; then
        echo "FATAL: ${desktop} absent — upstream no longer installs the desktop entry this recipe hides" >&2
        return 1
    fi
    if grep -q '^NoDisplay=' "$desktop"; then
        echo "FATAL: ${desktop} already declares NoDisplay — upstream changed; re-derive this edit" >&2
        return 1
    fi
    echo "NoDisplay=true" >> "$desktop"
    if ! grep -qx 'NoDisplay=true' "$desktop"; then
        echo "FATAL: NoDisplay=true did not land in ${desktop}" >&2
        return 1
    fi
}

post_install() {
    set -e
    # The NoDisplay append that used to live here moved to do_install (see the
    # note there): it produced no packaged bytes and mutated a manifest-claimed
    # file on every target. What remains is live-tree cache maintenance, which
    # is what a post-install hook is for.
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true
}
