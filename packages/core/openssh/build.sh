#!/bin/bash
# OpenSSH 10.2p1
# BLFS 13.0 — with PAM support and InterGenOS systemd unit
#
# DESTDIR supported. Post-install creates sshd user/group, PAM config,
# installs systemd unit, and generates host keys.

configure() {
    set -e
    ./configure --prefix=/usr                            \
                --sysconfdir=/etc/ssh                    \
                --with-privsep-path=/var/lib/sshd        \
                --with-default-path=/usr/bin             \
                --with-superuser-path=/usr/sbin:/usr/bin \
                --with-pid-dir=/run                      \
                --with-pam
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # Tests require gdb and a running sshd — skip in chroot
    :
}

do_install() {
    set -e
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
    set -e
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
    # Remove pam_lastlog.so — deprecated and removed from Linux-PAM >= 1.6.0
    sed -i '/pam_lastlog\.so/d' /etc/pam.d/sshd
    chmod 644 /etc/pam.d/sshd

    # Enable PAM in sshd_config
    echo "UsePAM yes" >> /etc/ssh/sshd_config

    # D-007 — explicitly disable root SSH login. Ship a drop-in rather
    # than sed-replacing upstream sshd_config so future upstream rebases
    # cannot silently revert the posture.
    install -dm755 /etc/ssh/sshd_config.d
    cat > /etc/ssh/sshd_config.d/00-intergenos-d007.conf << "EOF"
# InterGenOS — D-007 SSH posture
# Source-of-truth: docs/owner-directives.md D-007 (2026-05-18)
#
# SSH is enabled for the user account only. Root SSH is not permitted
# on any lane (live ISO, qcow2, installed system). This drop-in ships
# in /etc/ssh/sshd_config.d/ so future upstream-config rebases cannot
# silently revert the posture.

PermitRootLogin no
EOF
    chmod 644 /etc/ssh/sshd_config.d/00-intergenos-d007.conf

    # D-007 — NO pre-installed SSH host keys. Host keys are generated
    # at first boot by sshd.service's ExecStartPre guard
    # ('test -f /etc/ssh/ssh_host_ed25519_key || ssh-keygen -A').
    # Generating host keys at build time would bake the SAME keys into
    # every shipped install — trivially-exploitable impersonation
    # across every installed system. Removed per D-007.

    # Enable sshd service. SSH is on by default; root login is blocked
    # by the drop-in above; only the user-chosen sudo-capable account
    # (Forge) or the live `intergenos` user can SSH in.
    systemctl enable sshd.service
}
