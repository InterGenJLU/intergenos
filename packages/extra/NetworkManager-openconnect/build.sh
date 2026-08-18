#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# NetworkManager-openconnect 1.2.10 — the NetworkManager plugin for
# OpenConnect, which speaks the Cisco AnyConnect, Juniper, GlobalProtect and
# Fortinet SSL-VPN protocols.
#
# Build system verified against the pinned tarball's configure.ac:
#   - autotools with a generated ./configure (GNOME release tarball).
#   - Required modules and the line each is checked on: glib-2.0 >= 2.34 (109),
#     gmodule-2.0 (113), libxml-2.0 (114), libsecret-1 >= 0.18 (117),
#     gtk+-3.0 >= 3.12 (121), libnma >= 1.2.0 (124), gcr-3 >= 3.4 (126),
#     webkit2gtk-4.1 with a 4.0 fallback (148), libnm >= 1.2.0 (152) and
#     openconnect >= 3.02 (168).
#   - Two of those are worth stating because the obvious tree package is the
#     wrong one: the GTK3 WebKit comes from the webkitgtk-gtk3 package
#     (/usr/lib/libwebkit2gtk-4.1.so), NOT from webkitgtk, which this tree
#     builds with USE_GTK4=ON and which therefore provides only WebKit 6.0;
#     and gcr-3 comes from the gcr package (3.41.2), not from gcr4.
#   - --with-authdlg is left at its default (on) and every dependency it needs
#     exists in the tree, so the authentication dialog is built. Turning it off
#     would remove the only interface through which a user answers a VPN
#     server's authentication form.
#   - --without-libnm-glib is passed because that compatibility layer is
#     deprecated upstream and this tree has no libnm-glib package; the option
#     exists in this configure.ac (line 75), unlike in the OpenVPN plugin's.
#   - --with-gtk4 is NOT passed, for the same reason as the OpenVPN plugin:
#     libnma-gtk4 is not packaged here, and libnma (GTK3) is.

configure() {
    set -e
    ./configure                          \
        --prefix=/usr                    \
        --sysconfdir=/etc                \
        --localstatedir=/var             \
        --libexecdir=/usr/libexec        \
        --mandir=/usr/share/man          \
        --with-gnome                     \
        --with-authdlg                   \
        --without-libnm-glib
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
