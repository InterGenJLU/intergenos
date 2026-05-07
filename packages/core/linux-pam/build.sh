#!/bin/bash
# Linux-PAM 1.7.2 — Pluggable Authentication Modules
# BLFS 13.0
# IMPORTANT: Shadow and Systemd must be reinstalled after this

configure() {
    set -e
    # Create test config for build
    install -v -m755 -d /etc/pam.d

    cat > /etc/pam.d/other << "EOF"
auth     required       pam_deny.so
account  required       pam_deny.so
password required       pam_deny.so
session  required       pam_deny.so
EOF

    mkdir build
    cd    build

    meson setup ..        \
      --prefix=/usr       \
      --libdir=/usr/lib   \
      --buildtype=release \
      -D docdir=/usr/share/doc/Linux-PAM-1.7.2
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    cd build
    ninja test || true
    # Remove test config
    rm -fv /etc/pam.d/other
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
    chmod -v 4755 "${DESTDIR}/usr/sbin/unix_chkpwd"
}

post_install() {
    set -e
    # Create PAM configuration files
    install -vdm755 /etc/pam.d

    cat > /etc/pam.d/system-account << "EOF"
# Begin /etc/pam.d/system-account
account   required    pam_unix.so
# End /etc/pam.d/system-account
EOF

    cat > /etc/pam.d/system-auth << "EOF"
# Begin /etc/pam.d/system-auth
auth      required    pam_unix.so
# End /etc/pam.d/system-auth
EOF

    cat > /etc/pam.d/system-session << "EOF"
# Begin /etc/pam.d/system-session
session   required    pam_unix.so
session   required    pam_loginuid.so
session   optional    pam_systemd.so
# End /etc/pam.d/system-session
EOF

    # systemd user session PAM config (required for GNOME/GDM)
    cat > /etc/pam.d/systemd-user << "EOF"
# Begin /etc/pam.d/systemd-user
account  required    pam_access.so
account  include     system-account
session  required    pam_env.so
session  required    pam_limits.so
session  required    pam_loginuid.so
session  optional    pam_keyinit.so force revoke
session  optional    pam_systemd.so
auth     required    pam_deny.so
password required    pam_deny.so
# End /etc/pam.d/systemd-user
EOF

    cat > /etc/pam.d/system-password << "EOF"
# Begin /etc/pam.d/system-password
password  required    pam_unix.so       yescrypt shadow try_first_pass
# End /etc/pam.d/system-password
EOF

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
