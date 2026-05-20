#!/bin/bash
# Shadow 4.19.3
# LFS 13.0 Section 8.29
#
# DESTDIR works (autotools), but post-install commands
# (pwconv, grpconv, useradd, passwd) MUST run on the live system.

configure() {
    set -e
    # Disable installation of the groups program (provided by coreutils)
    sed -i 's/groups$(EXEEXT) //' src/Makefile.in
    find man -name Makefile.in -exec sed -i 's/groups\.1 / /'   {} \;
    find man -name Makefile.in -exec sed -i 's/getspnam\.3 / /' {} \;
    find man -name Makefile.in -exec sed -i 's/passwd\.5 / /'   {} \;

    # Use YESCRYPT for password hashing, fix mail spool and PATH
    sed -e 's:#ENCRYPT_METHOD DES:ENCRYPT_METHOD YESCRYPT:' \
        -e 's:/var/spool/mail:/var/mail:'                   \
        -e '/PATH=/{s@/sbin:@@;s@/bin:@@}'                  \
        -i etc/login.defs

    # Needed because passwd location is hardcoded in some programs
    touch /usr/bin/passwd

    ./configure --sysconfdir=/etc   \
        --disable-static            \
        --with-{b,yes}crypt         \
        --without-libbsd            \
        --disable-logind            \
        --with-group-name-max-length=32
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" exec_prefix=/usr install
    make DESTDIR="$DESTDIR" -C man install-man

    # Create default directory for useradd config
    mkdir -pv "${DESTDIR}/etc/default"

    # Set setuid bits — required for non-root users to change passwords,
    # switch users, etc. Must be set here because tar-based deployment
    # strips setuid bits during extraction.
    chmod 4755 "${DESTDIR}/usr/bin/passwd"
    chmod 4755 "${DESTDIR}/usr/bin/su"
    chmod 4755 "${DESTDIR}/usr/bin/chage"
    chmod 4755 "${DESTDIR}/usr/bin/chfn"
    chmod 4755 "${DESTDIR}/usr/bin/chsh"
    chmod 4755 "${DESTDIR}/usr/bin/newgrp"
    chmod 4755 "${DESTDIR}/usr/bin/expiry"
    chmod 4755 "${DESTDIR}/usr/bin/gpasswd"
}

# Post-install: runs on the live system AFTER deploy
post_install() {
    set -e
    # Enable shadow passwords
    pwconv
    grpconv

    # Set default group for new users
    useradd -D --gid 999

    # D-007 — root is locked on shipped installed systems. No valid
    # password, no SSH-as-root (enforced by sshd_config.d drop-in
    # shipped by core/openssh), no console-as-root with a known
    # credential. Privilege escalation happens via the user-chosen
    # sudo-capable account created by Forge (TUI/GUI install) or via
    # the `intergenos` sudo-capable user on the live ISO.
    # Source-of-truth: docs/owner-directives.md D-007 (2026-05-18).
    passwd -l root

    # Create tester user for running test suites as non-root
    # (LFS uses this for packages like gcc, coreutils, findutils)
    if ! id tester >/dev/null 2>&1; then
        useradd -m -s /bin/bash tester
    fi
}
