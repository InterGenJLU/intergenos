#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# openconnect 9.21 — client for the SSL-VPN protocols used by Cisco
# AnyConnect, Juniper, Palo Alto GlobalProtect and Fortinet gateways.
#
# Build system verified against the pinned tarball:
#   - autotools with a generated ./configure.
#   - Makefile.am installs sbin_PROGRAMS = openconnect and man8_MANS =
#     openconnect.8.
#   - Required modules, by configure.ac line: gnutls (458), p11-kit-1 (492),
#     libtasn1 (751), liblz4 (910), libxml-2.0 (974).
#
# TLS backend: GnuTLS, named explicitly. Upstream supports either GnuTLS or
# OpenSSL and picks by search order; both are core packages here, so leaving it
# implicit would let the crypto backend of every VPN session be decided by the
# order libraries happen to appear in the chroot. GnuTLS is chosen because
# upstream develops and tests openconnect against it first and because its
# PKCS#11 path through p11-kit is the one openconnect's smartcard support is
# written to.
#
# THE VPNC-SCRIPT REQUIREMENT, stated because it is the reason this package has
# a dependency most distributions' recipes bury: openconnect does not configure
# routes or DNS itself. It executes a helper when the tunnel comes up, and its
# configure will STOP the build if no such helper is found in the standard
# locations, printing a message that tells the packager to supply the path where
# the script will be installed (configure.ac:181-220). Passing a path without
# shipping the file would satisfy configure and leave a user with a tunnel that
# carries no traffic. The vpnc-scripts package in this same wave installs
# /etc/vpnc/vpnc-script, which is the path named below.

configure() {
    set -e
    ./configure                                       \
        --prefix=/usr                                 \
        --sbindir=/usr/sbin                           \
        --sysconfdir=/etc                             \
        --localstatedir=/var                          \
        --mandir=/usr/share/man                       \
        --with-vpnc-script=/etc/vpnc/vpnc-script      \
        --without-openssl                             \
        --disable-nls
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
