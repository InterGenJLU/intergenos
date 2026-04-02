#!/bin/bash
# Sudo 1.9.17p2 — Privilege escalation
# BLFS 13.0

configure() {
    ./configure --prefix=/usr         \
                --libexecdir=/usr/lib \
                --with-secure-path    \
                --with-env-editor     \
                --docdir=/usr/share/doc/sudo-1.9.17p2 \
                --with-passprompt="[sudo] password for %p: "
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    env LC_ALL=C make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}

post_install() {
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
