#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# openldap 2.6.12 — Open source LDAP implementation
# BLFS 13.0 — Full server installation

configure() {
    set -e
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.
    autoconf

    ./configure --prefix=/usr         \
                --sysconfdir=/etc     \
                --localstatedir=/var  \
                --libexecdir=/usr/lib \
                --disable-static      \
                --disable-debug       \
                --with-tls=openssl    \
                --with-cyrus-sasl     \
                --without-systemd     \
                --enable-dynamic      \
                --enable-crypt        \
                --enable-spasswd      \
                --enable-slapd        \
                --enable-modules      \
                --enable-rlookups     \
                --enable-backends=mod \
                --disable-sql         \
                --disable-wt          \
                --enable-overlays=mod
}

build() {
    set -e
    make depend
    make
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Upstream's install target creates localstatedir/run as a side effect.
    # Never ship var/run/ members: /var/run is a symlink to /run on
    # installed systems (base-files r9) and an archive dir member would
    # materialize it as a real dir at install time (split-brain runtime
    # dirs). slapd's runtime pid/args paths resolve through the symlink.
    rm -rf "$DESTDIR/var/run"

    # Fix .la references to .so in slapd config files
    sed -e "s/\.la/.so/" \
        -i "${DESTDIR}/etc/openldap/slapd.conf" \
        -i "${DESTDIR}/etc/openldap/slapd.ldif" \
        -i "${DESTDIR}/etc/openldap/slapd.conf.default" \
        -i "${DESTDIR}/etc/openldap/slapd.ldif.default" 2>/dev/null || true

    install -v -dm755 "${DESTDIR}/usr/share/doc/openldap-${PKG_VERSION}"
    cp -vfr doc/{drafts,rfc,guide} \
            "${DESTDIR}/usr/share/doc/openldap-${PKG_VERSION}"

    # The slapd.d config directory ships as owned payload (hook-contract
    # wave), mode 700 as before. Its OWNERSHIP is restored by post_install:
    # the ldap account is created by systemd-sysusers at install time, which
    # runs after the archive is deployed, so a uid the archive names cannot
    # resolve at deploy time. Restoring attributes on a path the package
    # already owns is what a lifecycle hook is for.
    install -v -dm700 "${DESTDIR}/etc/openldap/slapd.d"
}

post_install() {
    set -e
    # ldap user/group is declared by /usr/lib/sysusers.d/openldap.conf.
    # Process it explicitly here so the user exists before the subsequent
    # `install -o ldap -g ldap` commands resolve ownership. Idempotent;
    # safe at both BUILD VM time (chroot's live /etc/passwd) and laptop
    # install time (chroot context; pkm canonical pre-hook may have
    # already run sysusers — re-run is a no-op).
    systemd-sysusers /usr/lib/sysusers.d/openldap.conf

    # Create LDAP database directory with proper ownership. /var/lib is
    # machine state and is created here; /etc/openldap/slapd.d ships as
    # payload from do_install and only has its ownership restored, since
    # the ldap account does not exist when the archive is deployed.
    install -v -dm700 -o ldap -g ldap /var/lib/openldap
    chown -v ldap:ldap /etc/openldap/slapd.d

    # Set security permissions on config files (contain admin password in plain text)
    chmod  -v 640     /etc/openldap/slapd.{conf,ldif}   2>/dev/null || true
    chown  -v root:ldap /etc/openldap/slapd.{conf,ldif} 2>/dev/null || true
}
