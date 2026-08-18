#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# NetworkManager-openvpn 1.12.5 — the NetworkManager plugin that lets the
# desktop create, store and start OpenVPN connections.
#
# Build system verified against the pinned tarball's configure.ac:
#   - autotools with a generated ./configure (GNOME release tarball).
#   - Required modules: gmodule-2.0 + glib-2.0 >= 2.34 (line 101),
#     libnm >= 1.52.2 (line 123) and, for the GNOME half, libsecret-1 >= 0.18
#     (106), gtk+-3.0 >= 3.4 (110) and libnma >= 1.8.0 (113).
#   - The tree's networkmanager is 1.56.0, which satisfies the libnm floor.
#   - --with-gtk4 is NOT passed. That option builds the dialog against
#     libnma-gtk4 instead of libnma; this tree ships libnma (GTK3) and its
#     gtk4 twin is not packaged, so the GTK3 path is the one whose dependencies
#     actually exist here. This is a build against what the distribution
#     provides, not a feature being switched off: the authentication dialog is
#     built either way.
#
# --enable-absolute-paths is deliberately omitted; upstream marks it as a
# development convenience that hard-codes build paths into the installed .name
# files.

configure() {
    set -e
    ./configure                          \
        --prefix=/usr                    \
        --sysconfdir=/etc                \
        --localstatedir=/var             \
        --libexecdir=/usr/libexec        \
        --mandir=/usr/share/man          \
        --with-gnome
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
