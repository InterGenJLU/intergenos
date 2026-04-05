#!/bin/bash
# OpenSSH 10.2p1
# BLFS 13.0 — with PAM support and InterGenOS systemd unit
#
# DESTDIR supported. Post-install creates sshd user/group, PAM config,
# installs systemd unit, and generates host keys.

configure() {
    ./configure --prefix=/usr                            \
                --sysconfdir=/etc/ssh                    \
                --with-privsep-path=/var/lib/sshd        \
                --with-default-path=/usr/bin             \
                --with-superuser-path=/usr/sbin:/usr/bin \
                --with-pid-dir=/run                      \
                --with-pam
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # Tests require gdb and a running sshd — skip in chroot
    :
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # Install ssh-copy-id utility (BLFS)
    install -v -m755 contrib/ssh-copy-id "${DESTDIR}/usr/bin"
    install -v -m644 contrib/ssh-copy-id.1 "${DESTDIR}/usr/share/man/man1"

    # Install documentation
    install -v -m755 -d "${DESTDIR}/usr/share/doc/openssh-10.2p1"
    install -v -m644 INSTALL LICENCE OVERVIEW README* \
        "${DESTDIR}/usr/share/doc/openssh-10.2p1"

    # Install InterGenOS sshd systemd unit
    install -v -Dm644 /mnt/intergenos/config/systemd/sshd.service \
        "${DESTDIR}/usr/lib/systemd/system/sshd.service"

    # Create tmpfiles.d config for /run/sshd
    install -v -Dm644 /dev/stdin "${DESTDIR}/usr/lib/tmpfiles.d/sshd.conf" << 'EOF'
d /run/sshd 755 root root -
EOF
}

# Post-install: runs on the live system AFTER deploy
post_install() {
    # Create privilege separation directory
    install -v -g sys -m700 -d /var/lib/sshd

    # Create sshd user and group for privilege separation
    if ! getent group sshd >/dev/null 2>&1; then
        groupadd -g 50 sshd
    fi
    if ! id sshd >/dev/null 2>&1; then
        useradd -c 'sshd PrivSep' \
                -d /var/lib/sshd   \
                -g sshd            \
                -s /bin/false      \
                -u 50 sshd
    fi

    # Create PAM config from shadow's login config (BLFS)
    sed 's@d/login@d/sshd@g' /etc/pam.d/login > /etc/pam.d/sshd
    chmod 644 /etc/pam.d/sshd

    # Enable PAM in sshd_config
    echo "UsePAM yes" >> /etc/ssh/sshd_config

    # Generate host keys if they don't exist
    ssh-keygen -A

    # Enable sshd service
    systemctl enable sshd.service
}
