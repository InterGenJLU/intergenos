#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Sudo 1.9.17p2 — Privilege escalation
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr         \
                --libexecdir=/usr/lib \
                --with-secure-path    \
                --with-env-editor     \
                --docdir=/usr/share/doc/sudo-1.9.17p2 \
                --with-passprompt="[sudo] password for %p: "
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        env LC_ALL=C make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Set setuid bit — sudo must run as root to escalate privileges.
    # Must be set here because tar-based deployment strips setuid bits.
    chmod 4755 "${DESTDIR}/usr/bin/sudo"

    # Configuration ships as owned, manifest-tracked payload (hooks may not
    # write package-ownable bytes). Byte- and mode-identical to the files the
    # retired post_install wrote on live targets.
    install -dm755 "${DESTDIR}/etc/sudoers.d" "${DESTDIR}/etc/pam.d"

    cat > "${DESTDIR}/etc/sudoers.d/00-sudo" << "EOF"
Defaults secure_path="/usr/sbin:/usr/bin"
%wheel ALL=(ALL) ALL
EOF
    chmod 644 "${DESTDIR}/etc/sudoers.d/00-sudo"

    cat > "${DESTDIR}/etc/pam.d/sudo" << "EOF"
# Begin /etc/pam.d/sudo
auth      include     system-auth
account   include     system-account
session   required    pam_env.so
session   include     system-session
# End /etc/pam.d/sudo
EOF
    chmod 644 "${DESTDIR}/etc/pam.d/sudo"
}
