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

post_install() {
    set -e
    # Enable the print scheduler.
    #
    # Unmasked. `systemctl enable` is an offline file operation: measured
    # 2026-08-19 in a chroot built from this systemd 259.1, enabling a PRESENT
    # unit returns 0 and writes the symlink, a repeat call returns 0, and the
    # only reachable failure is a unit that does not exist, which returns 1.
    # This package installs cups.service itself, so a non-zero means its own
    # unit is missing.
    #
    # KNOWN GAP: cups.service is not whitelisted in intergenos-base-files'
    # 80-intergenos-enable.preset, so the preset policy resolves it to
    # `disable` through the 99- catch-all. Measured on an installed system
    # 2026-08-19: the PRESET column of `systemctl list-unit-files cups.service`
    # reads disabled, the unit's STATE is disabled, and the scheduler is
    # nevertheless running — cups.socket is enabled and socket-activates it.
    # So this recipe's enable and the preset policy disagree, and unmasking
    # settles only whether a FAILING enable is visible. Which of the two should
    # win is a default-running-service decision, not a recipe fix.
    systemctl enable cups.service
}
