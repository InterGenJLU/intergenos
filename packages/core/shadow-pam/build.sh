#!/bin/bash
# Shadow 4.19.3 — rebuilt with Linux-PAM support
# BLFS 13.0 — "Reinstallation of Shadow" section
#
# This rebuilds shadow after Linux-PAM is installed so that login, su,
# passwd, and all PAM-aware authentication works correctly.

configure() {
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
    make -j${IGOS_JOBS}
}

do_install() {
    # pamddir= prevents installing shipped PAM configs (we create our own)
    make DESTDIR="$DESTDIR" exec_prefix=/usr pamddir= install
}

post_install() {
    # --- Configure /etc/login.defs for PAM ---
    # Comment out functions now handled by PAM modules
    install -v -m644 /etc/login.defs /etc/login.defs.orig
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
        sed -i "s/^${FUNCTION}/# &/" /etc/login.defs
    done

    # --- Create PAM configuration files ---

    cat > /etc/pam.d/login << "EOF"
# Begin /etc/pam.d/login

auth      optional    pam_faildelay.so  delay=3000000
auth      requisite   pam_nologin.so
auth      include     system-auth

account   required    pam_access.so
account   include     system-account

session   required    pam_env.so
session   required    pam_limits.so
session   optional    pam_lastlog.so
session   include     system-session
session   optional    pam_motd.so
session   optional    pam_mail.so      dir=/var/mail standard quiet

-password include     system-password

# End /etc/pam.d/login
EOF

    cat > /etc/pam.d/passwd << "EOF"
# Begin /etc/pam.d/passwd

password  include     system-password

# End /etc/pam.d/passwd
EOF

    cat > /etc/pam.d/su << "EOF"
# Begin /etc/pam.d/su

auth      sufficient  pam_rootok.so
auth      include     system-auth
auth      required    pam_wheel.so use_uid

account   include     system-account

session   required    pam_env.so
session   include     system-session

# End /etc/pam.d/su
EOF

    cat > /etc/pam.d/chage << "EOF"
# Begin /etc/pam.d/chage

auth      sufficient  pam_rootok.so
auth      include     system-auth

account   include     system-account

session   include     system-session

password  required    pam_permit.so

# End /etc/pam.d/chage
EOF

    for PROGRAM in chfn chgpasswd chpasswd chsh groupadd groupdel \
                   groupmems groupmod newusers useradd userdel usermod
    do
        install -v -m644 /etc/pam.d/chage "/etc/pam.d/${PROGRAM}"
        sed -i "s/chage/$PROGRAM/" "/etc/pam.d/${PROGRAM}"
    done

    cat > /etc/pam.d/other << "EOF"
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
}
