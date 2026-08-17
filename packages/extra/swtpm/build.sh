#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# swtpm 0.10.1 — software TPM emulator
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# Provides emulated TPM 1.2/2.0 devices to VMs over a socket + control
# channel (libvirt's <tpm model='tpm-crb'> backend). Required for
# Secure-Boot + measured-boot guest testing. Links libtpms; gnutls
# enables the swtpm_cert local CA tooling; libseccomp enables the
# runtime seccomp profile. CUSE is left out: the /dev/cuse interface
# needs the fuse CUSE stack and libvirt consumes the socket interface —
# no InterGenOS consumer of the cuse device exists.

configure() {
    set -e
    NOCONFIGURE=1 ./autogen.sh
    # --disable-tests: upstream's sanctioned switch ("tools only needed
    # for tests need not be installed"). configure otherwise hard-
    # requires socat/tcsd — tooling used exclusively by the make-check
    # suite, which this pipeline does not execute for custom builds.
    # The shipped swtpm binaries are identical either way.
    ./configure --prefix=/usr \
                --libdir=/usr/lib \
                --with-openssl \
                --with-gnutls \
                --without-cuse \
                --without-selinux \
                --disable-tests
}

build() {
    set -e
    make -j"$(nproc)"
}

do_install() {
    set -e
    make DESTDIR="${DESTDIR}" install

    # swtpm_localca (invoked by swtpm_setup on every vTPM manufacture)
    # requires its statedir to exist writable by user tss; upstream's
    # make install does not create it, and its absence is FATAL to any
    # libvirt <tpm> domain start ("Need read/write rights on statedir
    # /var/lib/swtpm-localca for user tss" — found 2026-07-17 on the
    # first InterGenOS virtualization host). Ship a tmpfiles fragment
    # so systemd-tmpfiles creates it at boot.
    install -Dm644 /dev/stdin \
        "${DESTDIR}/usr/lib/tmpfiles.d/swtpm.conf" <<'TMPFILES'
d /var/lib/swtpm-localca 0750 tss tss -
TMPFILES

    # The tmpfiles fragment above references user tss, which is declared
    # by tpm2-tss's sysusers fragment — a different package with no
    # dependency edge from this one. On a fresh install the package
    # manager fires each package's tmpfiles hook at its own install
    # moment, so an install order that places swtpm before tpm2-tss
    # fails the hook ("Failed to resolve user 'tss'") and the package
    # is marked degraded (hit on the ge9b-05 fresh install, 2026-07-19).
    # Ship an identical, idempotent sysusers declaration so swtpm is
    # self-contained and order-independent; systemd-sysusers merges
    # agreeing duplicate declarations cleanly. The pkm canonical hooks
    # fire sysusers before tmpfiles within a package, so this resolves
    # at install time regardless of order.
    install -Dm644 /dev/stdin \
        "${DESTDIR}/usr/lib/sysusers.d/swtpm.conf" <<'SYSUSERS'
#Type Name ID GECOS               Home directory Shell
u     tss  -  "tss user for tpm2"
SYSUSERS
}
