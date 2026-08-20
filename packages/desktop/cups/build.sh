#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# cups 2.4.16 — Common UNIX Printing System
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -i 's#@CUPS_HTMLVIEW@#firefox#' desktop/cups.desktop.in

    # Fix IPP runtime issue
    sed -i '/& ipp->prev)/s/prev/& \&\& ipp->prev->next == *attr/' cups/ipp.c

    ./configure --libdir=/usr/lib            \
                --with-rundir=/run/cups      \
                --with-system-groups=lpadmin \
                --with-docdir=/usr/share/cups/doc-${version}
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # The blanket suite mask was retired here 2026-08-19: it accepted every
    # failure the suite can produce, including one never seen before, which
    # is the unverified-claim class the security posture exists to kill. The
    # suite still runs; its result is now governed by the tests: block in
    # package.yml and reported by pkg_run_tests, so an environmental failure
    # is an announced waiver rather than an invisible pass.
    LC_ALL=C pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make -k check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    install -v -d -m755 "${DESTDIR}/usr/share/doc"
    ln -svnf ../cups/doc-${version} "${DESTDIR}/usr/share/doc/cups-${version}"

    # Client config + PAM config ship as owned payload (hook-contract wave).
    # Byte/mode-identical to the files the retired post_install wrote (644).
    install -dm755 "${DESTDIR}/etc/cups" "${DESTDIR}/etc/pam.d"
    echo "ServerName /run/cups/cups.sock" > "${DESTDIR}/etc/cups/client.conf"
    chmod 644 "${DESTDIR}/etc/cups/client.conf"

    cat > "${DESTDIR}/etc/pam.d/cups" << "EOF"
# Begin /etc/pam.d/cups
auth    include system-auth
account include system-account
session include system-session
# End /etc/pam.d/cups
EOF
    chmod 644 "${DESTDIR}/etc/pam.d/cups"
}

# No post_install hook. Default enablement of every unit this package ships is
# decided in one place — intergenos-base-files'
# /usr/lib/systemd/system-preset/80-intergenos-enable.preset — and applied by the
# `systemctl preset-all` pass the image build and the installer both run. A
# `systemctl enable` here was a second voice for the same decision and the preset
# pass reverted it, so the tree stated one default and shipped another. Decided
# 2026-08-19: the preset files own this; recipes do not enable their own units.
