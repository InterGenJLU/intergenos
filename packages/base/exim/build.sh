#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Exim 4.99.1 — Message Transfer Agent
# BLFS 13.0

configure() {
    set -e
    # Create the exim system user/group on the build chroot BEFORE
    # build() runs, because the generated Local/Makefile pins
    # EXIM_USER=exim and the build phase invokes a check that errors
    # with "Please review your build-time configuration" if the user
    # is not resolvable. The same sysusers.d entry ships to the
    # archive via the builder's overlay-files phase and is
    # re-processed at laptop install time by pkm's canonical pre-hook
    # + post_install below.
    systemd-sysusers "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/files/usr/lib/sysusers.d/exim.conf"
    # Create Local/Makefile from src/EDITME
    sed -e 's,^BIN_DIR.*$,BIN_DIRECTORY=/usr/sbin,'    \
        -e 's,^CONF.*$,CONFIGURE_FILE=/etc/exim.conf,' \
        -e 's,^EXIM_USER.*$,EXIM_USER=exim,'           \
        -e '/# USE_OPENSSL/s,^#,,' src/EDITME > Local/Makefile

    printf "USE_GDBM = yes\nDBMLIB = -lgdbm\n" >> Local/Makefile

    # Add PAM support
    sed -i '/# SUPPORT_PAM=yes/s,^#,,' Local/Makefile
    echo "EXTRALIBS=-lpam" >> Local/Makefile
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    install -v -Dm644 doc/exim.8 "${DESTDIR}/usr/share/man/man8/exim.8"
    install -vdm 755    "${DESTDIR}/usr/share/doc/exim-4.99.1"
    cp      -Rv doc/*   "${DESTDIR}/usr/share/doc/exim-4.99.1"
    ln -sfv exim "${DESTDIR}/usr/sbin/sendmail"
    # Archive carries /var/spool/exim as root-owned mode 750; post_install
    # chowns to exim:exim after the canonical sysusers hook creates the user.
    # (The configure-stage useradd that previously created the exim user on
    # the BUILD VM was removed when the exim user moved to sysusers.d.)
    install -v -d -m750 "${DESTDIR}/var/spool/exim"

    # Set setuid + setgid bits — exim needs setuid root + setgid exim
    # for sendmail-compat non-root mail submission and for queue
    # delivery (the queue directory at /var/spool/exim is exim:exim
    # mode 750). Mode 6755 per BLFS 13.0 exim-4.99.1 canonical. Must
    # be set here because tar-based deployment strips setuid/setgid
    # bits during extraction (pkm restores them from tarball metadata
    # post-extract; see pkm/installer.py:475-490). Ownership is set in
    # post_install on the live system because the PEP 706 data filter
    # in the deploy-extract path strips uid/gid.
    chmod 6755 "${DESTDIR}/usr/sbin/exim"

    # Owned config (hook-contract wave). The aliases append lands on the
    # upstream template this install stages, matching live targets byte-for-
    # byte; pam.d/exim identical to the retired hook's file (644).
    cat >> "${DESTDIR}/etc/aliases" << "EOF"
postmaster: root
MAILER-DAEMON: root
EOF
    chmod 644 "${DESTDIR}/etc/aliases"

    install -dm755 "${DESTDIR}/etc/pam.d"
    cat > "${DESTDIR}/etc/pam.d/exim" << "EOF"
# Begin /etc/pam.d/exim
auth    include system-auth
account include system-account
session include system-session
# End /etc/pam.d/exim
EOF
    chmod 644 "${DESTDIR}/etc/pam.d/exim"
}

post_install() {
    set -e
    install -v -d -m1777 /var/mail

    # Process this package's /usr/lib/sysusers.d/exim.conf entry now
    # so the exim user/group exist before the chowns below resolve.
    # The /usr/sbin/sendmail symlink picks up setuid via the target
    # binary.
    systemd-sysusers /usr/lib/sysusers.d/exim.conf
    chown -R exim:exim /var/spool/exim
    chown root:exim /usr/sbin/exim
    # The chown above clears the setuid+setgid bits on a regular file (kernel
    # behavior, even for root) — so this hook has been shipping exim INERT on
    # every install to date. Restore mode 6755 AFTER the chown. /usr/sbin/exim
    # is the symlink; chmod follows it to the versioned binary, matching
    # do_install. Package-local twin of the L29 staging-chokepoint strip.
    chmod 6755 /usr/sbin/exim

    # Aliases + PAM config moved to do_install (hook-contract wave):
    # exim owns /etc/aliases outright — sole writer in the corpus.
}
