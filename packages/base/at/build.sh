#!/bin/bash
# at 3.2.5 — Job scheduling
# BLFS 13.0

configure() {
    set -e
    # Create atd user/group
    groupadd -g 17 atd 2>/dev/null || true
    useradd -d /dev/null -c "atd daemon" -g atd -s /bin/false -u 17 atd 2>/dev/null || true

    ./configure --with-daemon_username=atd        \
                --with-daemon_groupname=atd       \
                SENDMAIL=/usr/sbin/sendmail       \
                --with-jobdir=/var/spool/atjobs   \
                --with-atspool=/var/spool/atspool \
                --with-systemdsystemunitdir=/usr/lib/systemd/system
}

build() {
    set -e
    make -j1
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make test
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install \
         docdir=/usr/share/doc/at-3.2.5 \
         atdocdir=/usr/share/doc/at-3.2.5

    # Set setuid bits — at + atd need setuid root for unprivileged users
    # to submit / dequeue jobs. Mode 4750 with group restriction to atd
    # per BLFS 13.0 canonical. Must be set here because tar-based
    # deployment strips setuid bits during extraction (pkm restores them
    # from tarball metadata post-extract; see pkm/installer.py:475-490).
    # Ownership is set in post_install on the live system because the
    # PEP 706 data filter in the deploy-extract path strips uid/gid.
    chmod 4750 "${DESTDIR}/usr/bin/at"
    chmod 4750 "${DESTDIR}/usr/sbin/atd"
}

post_install() {
    set -e
    # Ensure atd user/group exists on the live system (configure-stage
    # groupadd/useradd run in the build chroot, not on the target). Then
    # chown so the 4750 mode means atd-group-members + root, not root-only.
    getent group atd >/dev/null || groupadd -g 17 atd
    getent passwd atd >/dev/null || useradd -d /dev/null -c "atd daemon" -g atd -s /bin/false -u 17 atd
    chown root:atd /usr/bin/at /usr/sbin/atd

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
