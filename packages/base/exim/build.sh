#!/bin/bash
# Exim 4.99.1 — Message Transfer Agent
# BLFS 13.0

configure() {
    set -e
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
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    install -v -m644 doc/exim.8 "${DESTDIR}/usr/share/man/man8/exim.8"
    install -vdm 755    "${DESTDIR}/usr/share/doc/exim-4.99.1"
    cp      -Rv doc/*   "${DESTDIR}/usr/share/doc/exim-4.99.1"
    ln -sfv exim "${DESTDIR}/usr/sbin/sendmail"
    install -v -d -m750 -o exim -g exim "${DESTDIR}/var/spool/exim"

    # Set setuid + setgid bits — exim needs setuid root + setgid exim
    # for sendmail-compat non-root mail submission and for queue
    # delivery (the queue directory at /var/spool/exim is exim:exim
    # mode 750). Mode 6755 per BLFS 13.0 exim-4.99.1 canonical. Must
    # be set here because tar-based deployment strips setuid/setgid
    # bits during extraction (pkm restores them from tarball metadata
    # post-extract; see pkm/installer.py:475-490). Ownership is set in
    # post_install on the live system because the PEP 706 data filter
    # in the deploy-extract path strips uid/gid.
    chmod 6755 "${DESTDIR}/usr/sbin/exim"
}

post_install() {
    set -e
    install -v -d -m1777 /var/mail

    # Ensure exim user/group exists on the live system + chown the
    # privileged binary so the setgid bit grants effective gid exim
    # (which owns /var/spool/exim). Configure-stage groupadd/useradd
    # ran in the build chroot, not on the target. The /usr/sbin/sendmail
    # symlink picks up the setuid via the target binary.
    getent group exim >/dev/null || groupadd -g 31 exim
    getent passwd exim >/dev/null || useradd -d /dev/null -c "Exim Daemon" -g exim -s /bin/false -u 31 exim
    chown root:exim /usr/sbin/exim

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
