#!/bin/bash
# Exim 4.99.1 — Message Transfer Agent
# BLFS 13.0

configure() {
    # Create exim user/group
    groupadd -g 31 exim 2>/dev/null || true
    useradd -d /dev/null -c "Exim Daemon" -g exim -s /bin/false -u 31 exim 2>/dev/null || true

    # Create Local/Makefile from src/EDITME
    sed -e 's,^BIN_DIR.*$,BIN_DIRECTORY=/usr/sbin,'    \
        -e 's,^CONF.*$,CONFIGURE_FILE=/etc/exim.conf,' \
        -e 's,^EXIM_USER.*$,EXIM_USER=exim,'           \
        -e '/# USE_OPENSSL/s,^#,,' src/EDITME > Local/Makefile

    printf "USE_GDBM = yes\nDBMLIB = -lgdbm\n" >> Local/Makefile

    # Add PAM support
    sed -i '/# SUPPORT_PAM=yes/s,^#,,' Local/Makefile
    echo "EXTRALIBS=-lpam" >> Local/Makefile
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
    install -v -m644 doc/exim.8 "${DESTDIR}/usr/share/man/man8/exim.8"
    install -vdm 755    "${DESTDIR}/usr/share/doc/exim-4.99.1"
    cp      -Rv doc/*   "${DESTDIR}/usr/share/doc/exim-4.99.1"
    ln -sfv exim "${DESTDIR}/usr/sbin/sendmail"
    install -v -d -m750 -o exim -g exim "${DESTDIR}/var/spool/exim"
}

post_install() {
    install -v -d -m1777 /var/mail

    # Create aliases
    cat >> /etc/aliases << "EOF"
postmaster: root
MAILER-DAEMON: root
EOF

    # PAM configuration
    cat > /etc/pam.d/exim << "EOF"
# Begin /etc/pam.d/exim
auth    include system-auth
account include system-account
session include system-session
# End /etc/pam.d/exim
EOF
}
