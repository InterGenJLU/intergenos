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
    LC_ALL=C make -k check || true
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

post_install() {
    set -e
    systemctl enable cups.service 2>/dev/null || true
}
