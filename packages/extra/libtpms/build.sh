#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libtpms 0.10.2 — TPM 1.2 and TPM 2.0 emulation library
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# Software TPM implementation consumed by swtpm to provide emulated TPM
# devices to VMs (required for Secure-Boot + measured-boot guest testing).
# Both TPM 1.2 and TPM 2.0 are enabled; crypto backend is OpenSSL. The
# source is the upstream-signed git tag archive (no pre-generated
# configure), so autoreconf runs first (NOCONFIGURE skips autogen.sh's
# implicit configure so the flags stay explicit here).

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # Upstream fix cherry-picked from post-0.10.2 master (fc8820cf):
    # gcc 15's const-generic strstr() makes tpm_library.c:441 assign a
    # const char* to char*, and upstream's -Werror promotes the
    # discarded-qualifiers warning to a build failure. The patch is
    # upstream's own one-line const-correctness fix.
    patch -Np1 -i "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0001-fix-TPMLIB_GetPlaintext-const.patch"
    NOCONFIGURE=1 ./autogen.sh
    ./configure --prefix=/usr \
                --libdir=/usr/lib \
                --with-tpm1 \
                --with-tpm2 \
                --with-openssl
}

build() {
    set -e
    make -j"$(nproc)"
}

do_install() {
    set -e
    make DESTDIR="${DESTDIR}" install
}
