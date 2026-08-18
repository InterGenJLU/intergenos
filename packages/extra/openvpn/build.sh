#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# openvpn 2.7.6 — the TLS-based VPN daemon, used both directly and as the
# binary the NetworkManager-openvpn plugin drives.
#
# Build system verified against the pinned tarball:
#   - autotools with a generated ./configure (a CMakeLists.txt also exists
#     upstream; the autotools path is the one upstream documents for Unix and
#     the one every shipping distribution uses).
#   - src/openvpn/Makefile.am installs sbin_PROGRAMS = openvpn.
#   - distro/systemd/ holds openvpn-client@.service.in and
#     openvpn-server@.service.in plus tmpfiles-openvpn.conf; they are installed
#     by the build when --enable-systemd is on and the unit directory is given.
#
# Crypto backend: OpenSSL, stated explicitly rather than left to configure's
# search order. openssl is a core package here and mbedtls is not packaged, so
# naming the backend makes the recipe say which library the security of every
# tunnel actually rests on instead of leaving it to whatever configure finds.
#
# --enable-iproute2 is NOT passed: it makes openvpn call the iproute2 tools for
# interface setup instead of its own ioctl path. The default (ioctl) is what
# upstream tests most broadly; iproute2 remains a runtime dependency because
# the plugin and wg-style scripts around it invoke `ip` directly.

configure() {
    set -e
    ./configure                                            \
        --prefix=/usr                                      \
        --sbindir=/usr/sbin                                \
        --sysconfdir=/etc                                  \
        --localstatedir=/var                               \
        --mandir=/usr/share/man                            \
        --with-crypto-library=openssl                      \
        --enable-lzo                                       \
        --enable-lz4                                       \
        --enable-systemd                                   \
        --enable-dco                                       \
        --with-systemdunitdir=/usr/lib/systemd/system       \
        --with-tmpfilesdir=/usr/lib/tmpfiles.d
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
