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

    # D-007 — root is locked on shipped installed systems. No valid
    # password, no SSH-as-root (enforced by sshd_config.d drop-in
    # shipped by core/openssh), no console-as-root with a known
    # credential. Privilege escalation happens via the user-chosen
    # sudo-capable account created by Forge (TUI/GUI install) or via
    # the `intergenos` sudo-capable user on the live ISO.
    # The requirement is stated in scripts/check-d007-compliance.sh; the
    # locked-root check itself is scripts/check-d007-runtime.sh Gate D.
    #
    # IMPORTANT: use `usermod -p '!' root`, NOT `passwd -l root`.
    # Reason: scripts/chroot-build.sh ships an initial /etc/passwd that
    # has `root:x:...` (the canonical "see /etc/shadow" placeholder).
    # pwconv (called above) creates /etc/shadow by reading /etc/passwd
    # and inheriting that `x` into the shadow file's password field —
    # so /etc/shadow lands with `root:x:...`. `passwd -l` then PREFIXES
    # `!` to whatever's there, yielding `root:!x:...` — neither `*`
    # nor `!` nor `!*` nor `!!`, which is the set of canonical locked
    # sentinels accepted by scripts/check-d007-runtime.sh Gate D.
    # `usermod -p '!' root` writes the password field DIRECTLY to `!`
    # (independent of whatever pwconv put there), which is the
    # canonical locked sentinel + matches the gate's accepted set.
    # Closes the D-007 runtime gate failure surfaced 2026-05-24.
    usermod -p '!' root

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
