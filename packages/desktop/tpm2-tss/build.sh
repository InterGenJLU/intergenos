#!/bin/bash
# tpm2-tss 4.1.3 — TCG TPM2 Software Stack
# Provides libtss2-{esys,sys,mu,rc,tcti-*} for systemd's TPM2 features
# (cryptenroll, pcrlock, measured-boot policies). Required by security
# design for the project's measured-boot stance — without it systemd
# silently disables all TPM2 features at configure time.
#
# Build #5 audit: not in package set → systemd-pass2 silently dropped tpm2.

configure() {
    set -e
    # Upstream tarball ships with autotools pre-bootstrapped (./configure
    # exists). No bootstrap step needed.
    ./configure --prefix=/usr                              \
                --disable-static                           \
                --with-udevrulesdir=/usr/lib/udev/rules.d  \
                --with-tmpfilesdir=/usr/lib/tmpfiles.d     \
                --with-sysusersdir=/usr/lib/sysusers.d     \
                --enable-fapi                              \
                --enable-policy
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
