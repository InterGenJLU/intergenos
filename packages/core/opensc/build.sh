#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# opensc 0.27.1 — PKCS#11 modules + tools for PIV/PKCS#15 smart cards
#                 (DOWNSTREAM-PATCHED for Nitrokey NK1 RSA-4096 PIV)
#
# The PKCS#11 brain of the InterGenOS smartcard/PIV signing stack:
# opensc-pkcs11.so is the module the signing ceremony loads to drive the PIV
# applet on the NK1 (via pcscd + the ccid bundle). pkcs11-tool/opensc-tool/
# piv-tool drive provisioning. Build system: autotools (the release tarball
# is pre-bootstrapped; ./configure is present).
#
# --prefix=/usr (NOT /usr/local): on InterGenOS THIS patched build IS the
# system opensc — there is no distro opensc to avoid clobbering. The host
# reference build used /usr/local only because a distro opensc was present.
#
# DOWNSTREAM PATCH (applied BY HAND below, the gtk4 precedent — see
# docs/operations/08-adding-packages.md "Patching bundled / upstream source"):
# stock OpenSC gates CI_RSA_4096 to specific Yubikey firmware
# (card-piv.c switch case SC_CARD_TYPE_PIV_II_YUBIKEY4, yubico_version >=
# 0x00050700). The NK1 is not detected as a Yubikey type, so it never gets
# CI_RSA_4096 and the PIV mechanism table caps at RSA-3072. The patch forces
# CI_RSA_4096 for all PIV card types right after the switch block. Verified to
# apply cleanly with `patch -p1 --dry-run` against the pinned 0.27.1 tarball.

PKGDIR="${IGOS_PACKAGE_DIR:-/mnt/intergenos/packages/core/opensc}"

configure() {
    set -e

    # Apply the downstream RSA-4096 PIV patch by hand (do NOT route through the
    # package.yml `patches:` key — that auto-applier reads from /sources, not
    # the in-repo patches/ dir). Patch is a/-prefixed so -p1 strips cleanly.
    patch -p1 --dry-run < "${PKGDIR}/patches/0001-piv-force-rsa4096.patch"
    patch -p1           < "${PKGDIR}/patches/0001-piv-force-rsa4096.patch"

    # Option surface verified against the real 0.27.1 ./configure --help:
    #   - PKCS#11 module installs to ${libdir}/opensc-pkcs11.so (and a copy
    #     under ${libdir}/pkcs11/) -> /usr/lib/opensc-pkcs11.so.
    #   - docs are OFF by default (--enable-doc [disabled]); --disable-man
    #     skips the man pages. (There is no --disable-doc flag in 0.27.1.)
    #   - pcsc enabled by default; openssl/zlib/readline autodetected from the
    #     in-chroot libraries.
    ./configure --prefix=/usr     \
                --sysconfdir=/etc \
                --localstatedir=/var \
                --disable-man     \
                --enable-openssl  \
                --enable-zlib     \
                --enable-readline
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
