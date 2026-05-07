#!/bin/bash
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
    env LC_ALL=C make check || true
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Set setuid bit — sudo must run as root to escalate privileges.
    # Must be set here because tar-based deployment strips setuid bits.
    chmod 4755 "${DESTDIR}/usr/bin/sudo"
}

post_install() {
    set -e
    # Create sudoers drop-in
    cat > /etc/sudoers.d/00-sudo << "EOF"
Defaults secure_path="/usr/sbin:/usr/bin"
%wheel ALL=(ALL) ALL
EOF

    # PAM configuration for sudo
    cat > /etc/pam.d/sudo << "EOF"
# Begin /etc/pam.d/sudo
auth      include     system-auth
account   include     system-account
session   required    pam_env.so
session   include     system-session
# End /etc/pam.d/sudo
EOF
    chmod 644 /etc/pam.d/sudo
}
