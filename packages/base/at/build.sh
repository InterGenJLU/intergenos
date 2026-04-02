#!/bin/bash
# at 3.2.5 — Job scheduling
# BLFS 13.0

configure() {
    # Create atd user/group
    groupadd -g 17 atd 2>/dev/null || true
    useradd -d /dev/null -c "atd daemon" -g atd -s /bin/false -u 17 atd 2>/dev/null || true

    ./configure --with-daemon_username=atd        \
                --with-daemon_groupname=atd       \
                SENDMAIL=/usr/sbin/sendmail       \
                --with-jobdir=/var/spool/atjobs   \
                --with-atspool=/var/spool/atspool \
                --with-systemdsystemunitdir=/lib/systemd/system
}

build() {
    make -j1
}

check() {
    make test || true
}

do_install() {
    make DESTDIR="$DESTDIR" install \
         docdir=/usr/share/doc/at-3.2.5 \
         atdocdir=/usr/share/doc/at-3.2.5
}

post_install() {
    # PAM configuration
    cat > /etc/pam.d/atd << "EOF"
# Begin /etc/pam.d/atd
auth     required pam_unix.so
account  required pam_unix.so
password required pam_unix.so
session  required pam_unix.so
# End /etc/pam.d/atd
EOF

    systemctl enable atd
}
