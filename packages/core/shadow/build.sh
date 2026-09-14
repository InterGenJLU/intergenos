#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
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

    # Use YESCRYPT for password hashing, fix mail spool and PATH.
    # The /PATH=/ sed drops legacy /sbin:,/bin: (merged-/usr) from BOTH
    # ENV_SUPATH and ENV_PATH. That left ENV_PATH=PATH=/usr/bin, which
    # omits /usr/sbin — so regular users (e.g. intergenos) could not find
    # /usr/sbin tools like `ip`/`nft` (GBC001.2 fix). The trailing
    # ENV_PATH rewrite (later -e wins per-line) restores /usr/sbin while
    # keeping the merged-/usr layout. ENV_SUPATH (root) already had it.
    sed -e 's:#ENCRYPT_METHOD DES:ENCRYPT_METHOD YESCRYPT:' \
        -e 's:/var/spool/mail:/var/mail:'                   \
        -e '/PATH=/{s@/sbin:@@;s@/bin:@@}'                  \
        -e 's@^ENV_PATH.*@ENV_PATH\tPATH=/usr/local/bin:/usr/bin:/usr/local/sbin:/usr/sbin@' \
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

    # ROOT'S PASSWORD FIELD IS NOT TOUCHED HERE (decided 2026-09-14, R001.3
    # row 5). This hook runs in two places: inside the build chroot, and ON
    # EVERY INSTALLED TARGET after the installer has written the password the
    # person chose (installer/backend/users.py set_root_password, phase
    # "users"; this hook fires in the later "hooks" phase). Until 2026-09-14
    # it ended with `usermod -p '!' root`, so every install landed with root
    # locked and the rescue shell had no credential — proven on the Zephyrus
    # 2026-09-04 and confirmed on three more machines. D-007 (root locked on
    # shipped MEDIA) is now enforced where the media is assembled:
    # scripts/build-intergenos.sh locks root in the chroot right before the
    # D-007 runtime gate (scripts/check-d007-runtime.sh Gate D), and
    # scripts/create-image.sh locks it in the image. The installer verifies
    # after the hooks that root still carries the person's hash.

    # NOTE: do NOT create the LFS `tester` test-runner account here.
    # post_install is a canonical pkm hook that ALSO runs at INSTALL time on
    # the target, so a `useradd tester` here leaks a stray locked tester:1001
    # + /home/tester onto every installed system (root-caused on the GBC001.5
    # first bare-metal install). The build's test account is build-side only:
    # scripts/chroot-build.sh creates `tester` before Ch8 `make check`, and
    # scripts/chroot-build-ch8.sh removes it after the tests. The useradd that
    # used to live here was ALWAYS skipped during the build (chroot-build.sh's
    # tester pre-exists), so it only ever fired on the target — pure leak.
    # Same "canonical hook runs at install-time" class as the install #18 fix.
}
