#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# libp11 0.4.18 — PKCS#11 wrapper + OpenSSL engine/provider (engine_pkcs11)
#
# The OpenSSL-side bridge of the InterGenOS smartcard/PIV signing stack. libp11
# provides libp11.so plus the OpenSSL PKCS#11 ENGINE (pkcs11.so, the
# engine_pkcs11 component) and the OpenSSL 3 PROVIDER (pkcs11.so under
# ossl-modules). This is what lets OpenSSL-consuming tools (e.g. sbsign during
# a Secure Boot signing ceremony) reach the NK1 PIV applet through
# opensc-pkcs11.so -> pcscd -> ccid. Build system: autotools (the release
# tarball is pre-bootstrapped; ./configure is present).
#
# Install paths verified against the real 0.4.18 configure.ac + src/Makefile.am:
#   - lib_LTLIBRARIES = libp11.la            -> /usr/lib/libp11.so*
#   - enginesexec_LTLIBRARIES = pkcs11.la    -> enginesdir from
#       `pkg-config --variable=enginesdir libcrypto`; for our OpenSSL 3.6.1
#       that is /usr/lib/engines-3/pkcs11.so.
#   - providersexec_LTLIBRARIES = pkcs11prov.la -> modulesdir from
#       `pkg-config --variable=modulesdir libcrypto` -> /usr/lib/ossl-modules/
#       pkcs11prov.so (libp11 names the provider distinctly from the engine's
#       pkcs11.so so the two coexist; libpkcs11.so is a symlink to it).

configure() {
    set -e
    # enginesdir/modulesdir are auto-detected from libcrypto.pc (our in-chroot
    # OpenSSL 3.6.1); no --with-enginesdir/--with-modulesdir override needed.
    ./configure --prefix=/usr     \
                --sysconfdir=/etc \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
