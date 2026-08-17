#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Shadow 4.19.3 — rebuilt with Linux-PAM support
# BLFS 13.0 — "Reinstallation of Shadow" section
#
# This rebuilds shadow after Linux-PAM is installed so that login, su,
# passwd, and all PAM-aware authentication works correctly.

configure() {
    set -e
    # Disable groups program (provided by coreutils)
    sed -i 's/groups$(EXEEXT) //' src/Makefile.in
    find man -name Makefile.in -exec sed -i 's/groups\.1 / /'   {} \;
    find man -name Makefile.in -exec sed -i 's/getspnam\.3 / /' {} \;
    find man -name Makefile.in -exec sed -i 's/passwd\.5 / /'   {} \;

    # Use YESCRYPT for password hashing, fix mail spool and PATH
    sed -e 's@#ENCRYPT_METHOD DES@ENCRYPT_METHOD YESCRYPT@' \
        -e 's@/var/spool/mail@/var/mail@'                   \
        -e '/PATH=/{s@/sbin:@@;s@/bin:@@}'                  \
        -i etc/login.defs

    ./configure --sysconfdir=/etc   \
                --disable-static    \
                --without-libbsd    \
                --with-{b,yes}crypt
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    # pamddir= prevents installing shipped PAM configs (we create our own)
    make DESTDIR="$DESTDIR" exec_prefix=/usr pamddir= install

    # Set setuid bits — required for non-root users to change passwords,
    # switch users, etc. Must be set here because tar-based deployment
    # strips setuid bits during extraction. Empirically, shadow's
    # upstream `make install` does not reliably set mode 4755 in DESTDIR
    # — the same chmod block lives in packages/core/shadow/build.sh.
    # Because shadow-pam REINSTALLS these binaries (BLFS "Reinstallation
    # of Shadow"), it overlays the setuid bits shadow set, so the
    # restoration must be mirrored here.
    chmod 4755 "${DESTDIR}/usr/bin/passwd"
    chmod 4755 "${DESTDIR}/usr/bin/su"
    chmod 4755 "${DESTDIR}/usr/bin/chage"
    chmod 4755 "${DESTDIR}/usr/bin/chfn"
    chmod 4755 "${DESTDIR}/usr/bin/chsh"
    chmod 4755 "${DESTDIR}/usr/bin/newgrp"
    chmod 4755 "${DESTDIR}/usr/bin/expiry"
    chmod 4755 "${DESTDIR}/usr/bin/gpasswd"

    # The whole PAM-for-shadow configuration set ships as owned payload
    # (hook-contract wave): login.defs PAM-commenting applied at staging
    # (.orig = the pristine upstream copy, as on live targets), the pam.d
    # stack files byte-identical to the retired hook's output. /etc/pam.d/
    # other is OWNED HERE (shadow-pam's variant is the live-target bytes;
    # linux-pam's duplicate writer was retired in the same wave).
    install -dm755 "${DESTDIR}/etc/pam.d"
    # --- Configure /etc/login.defs for PAM ---
    # Comment out functions now handled by PAM modules
    install -m644 "${DESTDIR}/etc/login.defs" "${DESTDIR}/etc/login.defs.orig"
    for FUNCTION in FAIL_DELAY               \
                    FAILLOG_ENAB             \
                    LASTLOG_ENAB             \
                    MAIL_CHECK_ENAB          \
                    OBSCURE_CHECKS_ENAB      \
                    PORTTIME_CHECKS_ENAB     \
                    QUOTAS_ENAB              \
                    CONSOLE MOTD_FILE        \
                    FTMP_FILE NOLOGINS_FILE  \
                    ENV_HZ PASS_MIN_LEN      \
                    SU_WHEEL_ONLY            \
                    PASS_CHANGE_TRIES        \
                    PASS_ALWAYS_WARN         \
                    CHFN_AUTH ENCRYPT_METHOD \
                    ENVIRON_FILE
    do
        sed -i "s/^${FUNCTION}/# &/" "${DESTDIR}/etc/login.defs"
    done

    # --- Create PAM configuration files ---

    cat > "${DESTDIR}"/etc/pam.d/login << "EOF"
# Begin /etc/pam.d/login

auth      optional    pam_faildelay.so  delay=3000000
auth      requisite   pam_nologin.so
auth      include     system-auth

account   required    pam_access.so
account   include     system-account

session   required    pam_env.so
# pam_limits.so is NOT listed directly here: system-session (included
# below) already runs it. Listing it both places ran it TWICE, double-
# printing the limits banner (and the "too many logins" denial) on every
# login — and on sshd, which is sed-derived from this file (GBC001.2 fix).
#session   optional    pam_lastlog.so
session   include     system-session
session   optional    pam_motd.so
session   optional    pam_mail.so      dir=/var/mail standard quiet

-password include     system-password

# End /etc/pam.d/login
EOF

    cat > "${DESTDIR}"/etc/pam.d/passwd << "EOF"
# Begin /etc/pam.d/passwd

password  include     system-password

# End /etc/pam.d/passwd
EOF

    cat > "${DESTDIR}"/etc/pam.d/su << "EOF"
# Begin /etc/pam.d/su

auth      sufficient  pam_rootok.so
auth      include     system-auth
auth      required    pam_wheel.so use_uid

account   include     system-account

session   required    pam_env.so
session   include     system-session

# End /etc/pam.d/su
EOF

    cat > "${DESTDIR}"/etc/pam.d/chage << "EOF"
# Begin /etc/pam.d/chage

auth      sufficient  pam_rootok.so
auth      include     system-auth

account   include     system-account

session   include     system-session

password  required    pam_permit.so

# End /etc/pam.d/chage
EOF

    for PROGRAM in chfn chgpasswd chsh groupadd groupdel \
                   groupmems groupmod useradd userdel usermod
    do
        install -m644 "${DESTDIR}/etc/pam.d/chage" "${DESTDIR}/etc/pam.d/${PROGRAM}"
        sed -i "s/chage/$PROGRAM/" "${DESTDIR}/etc/pam.d/${PROGRAM}"
    done

    # BLFS: chpasswd and newusers need system-password, not pam_permit.so
    for PROGRAM in chpasswd newusers; do
        cat > "${DESTDIR}"/etc/pam.d/${PROGRAM} << CPEOF
# Begin /etc/pam.d/${PROGRAM}

auth      sufficient  pam_rootok.so
auth      include     system-auth

account   include     system-account

session   include     system-session

password  include     system-password

# End /etc/pam.d/${PROGRAM}
CPEOF
    done

    cat > "${DESTDIR}"/etc/pam.d/other << "EOF"
# Begin /etc/pam.d/other

auth        required        pam_warn.so
auth        required        pam_deny.so
account     required        pam_warn.so
account     required        pam_deny.so
password    required        pam_warn.so
password    required        pam_deny.so
session     required        pam_warn.so
session     required        pam_deny.so

# End /etc/pam.d/other
EOF

    # Live-target modes preserved (all 644)
    chmod 644 "${DESTDIR}"/etc/pam.d/login "${DESTDIR}"/etc/pam.d/passwd \
              "${DESTDIR}"/etc/pam.d/su "${DESTDIR}"/etc/pam.d/chage \
              "${DESTDIR}"/etc/pam.d/chpasswd "${DESTDIR}"/etc/pam.d/newusers \
              "${DESTDIR}"/etc/pam.d/other
}

