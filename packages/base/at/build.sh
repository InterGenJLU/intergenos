#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# at 3.2.5 — Job scheduling
# BLFS 13.0

configure() {
    set -e
    # Create the atd system user/group on the build chroot BEFORE the
    # subsequent `make install` invocation, which uses the Makefile-
    # baked `install -o atd -g atd` to create /var/spool/atjobs +
    # /var/spool/atspool. Without this, install fails with `invalid
    # user 'atd'`. The same sysusers.d entry is shipped to the archive
    # by the builder's overlay-files phase and re-processed at laptop
    # install time by pkm's canonical pre-hook + post_install below.
    systemd-sysusers "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/files/usr/lib/sysusers.d/at.conf"
    ./configure --with-daemon_username=atd        \
                --with-daemon_groupname=atd       \
                SENDMAIL=/usr/sbin/sendmail       \
                --with-jobdir=/var/spool/atjobs   \
                --with-atspool=/var/spool/atspool \
                --with-systemdsystemunitdir=/usr/lib/systemd/system
}

build() {
    set -e
    make -j1
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make test
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install \
         docdir=/usr/share/doc/at-3.2.5 \
         atdocdir=/usr/share/doc/at-3.2.5

    # Set setuid bits — at + atd need setuid root for unprivileged users
    # to submit / dequeue jobs. Mode 4750 with group restriction to atd
    # per BLFS 13.0 canonical. Must be set here because tar-based
    # deployment strips setuid bits during extraction (pkm restores them
    # from tarball metadata post-extract; see pkm/installer.py:475-490).
    # Ownership is set in post_install on the live system because the
    # PEP 706 data filter in the deploy-extract path strips uid/gid.
    chmod 4750 "${DESTDIR}/usr/bin/at"
    chmod 4750 "${DESTDIR}/usr/sbin/atd"

    # PAM configuration ships as owned payload (hook-contract wave: hooks may
    # not write package-ownable bytes). Byte/mode-identical to the file the
    # retired post_install block wrote on live targets (644).
    install -dm755 "${DESTDIR}/etc/pam.d"
    cat > "${DESTDIR}/etc/pam.d/atd" << "EOF"
# Begin /etc/pam.d/atd
auth     required pam_unix.so
account  required pam_unix.so
password required pam_unix.so
session  required pam_unix.so
# End /etc/pam.d/atd
EOF
    chmod 644 "${DESTDIR}/etc/pam.d/atd"
}

post_install() {
    set -e
    # Process this package's /usr/lib/sysusers.d/at.conf entry now so
    # the atd user/group exist before the chown below resolves. Then
    # chown so the 4750 mode means atd-group-members + root, not
    # root-only.
    systemd-sysusers /usr/lib/sysusers.d/at.conf
    chown root:atd /usr/bin/at /usr/sbin/atd
    # The chown above clears the setuid bit on a regular file (kernel behavior,
    # even for root) — so this hook has been shipping at/atd INERT on every
    # install to date. Restore the 4750 mode AFTER the chown. Package-local
    # twin of the L29 staging-chokepoint strip; mode per do_install / BLFS 13.0.
    chmod 4750 /usr/bin/at /usr/sbin/atd

    # Spool ownership: the sealed archive ships no spool paths (the Makefile
    # creates them outside DESTDIR capture), so whatever creates them on the
    # target leaves root:root — measured on an installed ge9b-12 system —
    # and atd cannot write its own spool. install -d creates-or-corrects
    # either way: the Makefile's canonical atd:atd 1770 spool dirs, plus
    # atd-owned .SEQ when present (600, no setuid bit, no re-chmod hazard).
    install -d -m1770 -o atd -g atd /var/spool/atjobs /var/spool/atspool
    [ -f /var/spool/atjobs/.SEQ ] && chown atd:atd /var/spool/atjobs/.SEQ

    systemctl enable atd
}
