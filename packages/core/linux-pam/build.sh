#!/bin/bash
# Linux-PAM 1.7.2 — Pluggable Authentication Modules
# BLFS 13.0
# IMPORTANT: Shadow and Systemd must be reinstalled after this

configure() {
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
    cd build
    ninja
}

check() {
    cd build
    ninja test || true
    # Remove test config
    rm -fv /etc/pam.d/other
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
    chmod -v 4755 "${DESTDIR}/usr/sbin/unix_chkpwd"
}

post_install() {
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
# End /etc/pam.d/system-session
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
