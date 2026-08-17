#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
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

    # GBC002.4 (2026-06-08): make the sshd_config.d/ drop-ins actually take
    # effect. Upstream OpenSSH's sshd_config ships with NO `Include` directive,
    # so the 00-intergenos-d007.conf (PermitRootLogin no) and
    # 01-intergenos-hardening.conf (Mozilla-Modern crypto, MaxAuthTries 3,
    # PrintMotd no, ...) drop-ins were SILENTLY INERT on every build — sshd ran
    # on upstream defaults. Verified on the GBC002 A12 install: `sshd -T` showed
    # maxauthtries 6, printmotd yes (the double-banner), permitrootlogin
    # prohibit-password (NOT the D-007 `no`). sshd is FIRST-match-wins, so the
    # Include must sit at the TOP of sshd_config to win over the main config's
    # subsequent PasswordAuthentication/PermitRootLogin defaults. Stage it into
    # the PACKAGED config (DESTDIR) so it reaches both the live ISO and every
    # Forge-installed target (not relied on via post_install).
    sed -i '1i Include /etc/ssh/sshd_config.d/*.conf' "${DESTDIR}/etc/ssh/sshd_config"

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

    # L30 (2026-07-05): the curated PAM stack now ships STATICALLY via
    # files/etc/pam.d/sshd (the pkg_stage files/ overlay carries it, same
    # as the sshd_config.d drop-ins). It was previously sed-generated
    # from /etc/pam.d/login by post_install at install time, which made
    # the content depend on package install ORDER — Forge installs ran
    # openssh's hook before the curated login deployed, so installed
    # systems got upstream's stock stub (pam_securetty/pam_selinux/
    # pam_console) and ssh died at the PAM account phase. Same lesson as
    # the sshd_config Include fix above: package the config so the
    # archive, the live ISO, and every Forge-installed target carry
    # identical bytes.

    # UsePAM belongs in the packaged config too (it was a post_install
    # append — which also duplicated the line on every reinstall).
    echo "UsePAM yes" >> "${DESTDIR}/etc/ssh/sshd_config"

    # NOTE: /run/sshd is created by sshd.service's RuntimeDirectory=sshd
    # directive (see config/systemd/sshd.service:44). A standalone
    # tmpfiles.d/sshd.conf is redundant and caused a duplicate-line
    # warning during systemd-tmpfiles --create at install time
    # (2026-05-27 install #28 trace, anomaly C). systemd creates the
    # runtime dir at unit start with the same 755 perms.

    # D-007 + hardening drop-ins ship as owned payload (hook-contract wave).
    # Byte/mode-identical to the files the retired hook blocks wrote (644).
    # D-007 — explicitly disable root SSH login. Ship a drop-in rather
    # than sed-replacing upstream sshd_config so future upstream rebases
    # cannot silently revert the posture.
    install -dm755 "${DESTDIR}/etc/ssh/sshd_config.d"
    cat > "${DESTDIR}/etc/ssh/sshd_config.d/00-intergenos-d007.conf" << "EOF"
# InterGenOS — D-007 SSH posture (decided 2026-05-18)
#
# SSH is enabled for the user account only. Root SSH is not permitted
# on any lane (live ISO, qcow2, installed system). This drop-in ships
# in /etc/ssh/sshd_config.d/ so future upstream-config rebases cannot
# silently revert the posture.

PermitRootLogin no
EOF
    chmod 644 "${DESTDIR}"/etc/ssh/sshd_config.d/00-intergenos-d007.conf

    # InterGenOS SSH hardening profile drop-in. Lands alongside the
    # 00-intergenos-d007.conf drop-in above. Applies whenever the SSH
    # server is enabled (the enable decision is now Forge-opt-in per
    # D-019; this drop-in defines what the SSH server looks like once
    # turned on). Mozilla Modern crypto profile + brute-force / DoS
    # mitigations + 10-minute idle-session timeout. Source-of-truth
    # references: wiki.mozilla.org/Security/Guidelines/OpenSSH (Modern
    # server profile, captures the curated crypto-algorithm set); the
    # non-crypto values were decided 2026-05-22 on usability grounds
    # (ClientAliveInterval 600 / MaxAuthTries 3 / LoginGraceTime 30 /
    # no AllowUsers whitelist).
    cat > "${DESTDIR}/etc/ssh/sshd_config.d/01-intergenos-hardening.conf" << "EOF"
# InterGenOS — SSH hardening profile
# Source-of-truth: Mozilla Modern OpenSSH server guidelines
#   (wiki.mozilla.org/Security/Guidelines/OpenSSH)
# The non-crypto values were decided 2026-05-22 on usability grounds.
#
# Lands alongside 00-intergenos-d007.conf (PermitRootLogin no). Both
# drop-ins ship in /etc/ssh/sshd_config.d/ and take effect via the
# `Include /etc/ssh/sshd_config.d/*.conf` line do_install prepends to the
# TOP of sshd_config. sshd is FIRST-match-wins, so the Include must sit
# above the main config's directives for these values to win — an Include
# at the bottom, or no Include at all, leaves these drop-ins INERT (the
# GBC002.4 bug fixed 2026-06-08: there was no Include, so sshd ran on
# upstream defaults despite these files being present on disk).
#
# Applies only when sshd is actually running. Per D-019 sshd is opt-in
# via Forge install (default OFF); this drop-in defines the running
# server's posture, not whether it runs.

# Brute-force / DoS mitigations.
# - MaxAuthTries: disconnect after 3 failed password attempts (default
#   was 6). Tightens credential-guessing window while leaving room for
#   one or two fat-finger typos before reconnect.
# - LoginGraceTime: kick anyone who hasn't completed login in 30s
#   (default was 2m). Humans don't need 2 minutes; 30s is plenty for
#   keyboard-interactive auth + password entry. Limits TCP-connection-
#   slot consumption from slowloris-style connection holds.
MaxAuthTries 3
LoginGraceTime 30

# Idle-session timeout. ClientAliveInterval sends a no-op keepalive
# probe every 600s (10 minutes); ClientAliveCountMax 0 means a single
# missed probe disconnects. Effective behavior: an SSH session with
# zero activity for 10 minutes gets dropped. Tradeoff: prevents
# walked-away-from-terminal session-takeover risk, occasional friction
# for users who keep SSH sessions open while doing other things.
ClientAliveInterval 600
ClientAliveCountMax 0

# Mozilla Modern crypto profile. Drops legacy algorithms (SHA-1 MACs,
# CBC-mode ciphers, RSA-SHA1 host keys, etc.) while keeping everything
# OpenSSH clients from the last ~7 years support. Source: Mozilla
# Modern server profile reference list.
#
# Post-quantum KEX prepended (PI-15): mlkem768x25519-sha256 (FIPS 203 /
# NIST ML-KEM, OpenSSH default since 10.0) and sntrup761x25519 are offered
# FIRST so a harvest-now-decrypt-later / Mythos-class adversary cannot
# force a classical-only handshake. Classical curve25519/ecdh/dh-gex stay
# for fallback with older clients. Keep this line BYTE-IDENTICAL with the
# copy in openssh build.sh (possible byte-equality gate).
KexAlgorithms mlkem768x25519-sha256,sntrup761x25519-sha512@openssh.com,curve25519-sha256@libssh.org,curve25519-sha256,ecdh-sha2-nistp521,ecdh-sha2-nistp384,ecdh-sha2-nistp256,diffie-hellman-group-exchange-sha256
Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com,aes256-ctr,aes192-ctr,aes128-ctr
MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com,umac-128-etm@openssh.com
HostKeyAlgorithms ssh-ed25519,ssh-ed25519-cert-v01@openssh.com,rsa-sha2-512,rsa-sha2-256,ecdsa-sha2-nistp521,ecdsa-sha2-nistp384,ecdsa-sha2-nistp256

# Verbose logging per Mozilla Modern. Captures session-establishment
# detail (which key was used, where the connection came from, etc.)
# without leaking secret material. Useful for post-incident audit.
LogLevel VERBOSE

# Suppress sshd's own /etc/motd print. pam_motd already prints the
# motd via the PAM session stack (shadow-pam ships pam_motd.so in
# /etc/pam.d/login, which /etc/pam.d/sshd inherits via the install-
# time sed from login -> sshd). Without PrintMotd=no, the banner is
# printed twice on login — once by sshd and once by pam_motd
# (2026-05-27 install #28 trace, anomaly F). Keeping pam_motd as the
# canonical motd-print path so the same banner shows on local TTY
# and remote SSH alike.
PrintMotd no
EOF
    chmod 644 "${DESTDIR}"/etc/ssh/sshd_config.d/01-intergenos-hardening.conf

    # D-007 — NO pre-installed SSH host keys. Host keys are generated
    # at first boot by sshd.service's ExecStartPre guard
    # ('test -f /etc/ssh/ssh_host_ed25519_key || ssh-keygen -A').
    # Generating host keys at build time would bake the SAME keys into
    # every shipped install — trivially-exploitable impersonation
    # across every installed system. Removed per D-007.
}

# Post-install: runs on the live system AFTER deploy
post_install() {
    set -e
    # Create privilege separation directory
    install -v -g sys -m700 -d /var/lib/sshd

    # sshd user/group is declared by /usr/lib/sysusers.d/openssh.conf
    # and created by the pkm canonical sysusers hook before this
    # lifecycle hook runs.

    # L30 (2026-07-05): /etc/pam.d/sshd and the UsePAM directive are now
    # PACKAGED in do_install (static files/etc/pam.d/sshd + the DESTDIR
    # sshd_config append). The old install-time sed-from-login generation
    # was install-order-dependent (see files/etc/pam.d/sshd header) and
    # the UsePAM append duplicated on every reinstall. Nothing PAM-related
    # remains in post_install by design.

    # The D-007 + hardening drop-ins moved to do_install (hook-contract
    # wave): they ship as owned, manifest-tracked payload now.

    # sshd.service is intentionally NOT enabled here. Per D-019 (2026-
    # 05-22, amends D-007 sshd-default arm), the SSH server is opt-in
    # via the Forge install UI (default OFF). The installer backend's
    # PHASE_SERVICES path (installer/backend/users.py:enable_ssh_server)
    # conditionally enables sshd.service AND opens TCP/22 in the
    # firewall on the YES path; package install alone leaves the
    # service installed-but-disabled. User can opt in later via
    # `systemctl enable --now sshd` + adding a TCP/22 accept rule to
    # /etc/nftables.conf. The PermitRootLogin no drop-in + no-baked-
    # host-keys posture from D-007 stays in force regardless.
}
